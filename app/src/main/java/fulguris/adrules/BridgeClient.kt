package fulguris.adrules

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import timber.log.Timber
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.spec.IvParameterSpec
import javax.crypto.spec.SecretKeySpec
import javax.inject.Inject
import javax.inject.Singleton

/**
 * HTTP client for the community bridge, handling the free host's JS challenge.
 *
 * The PHP host in front of the bridge answers every request without a valid
 * `__test` cookie with an ~850 byte HTML page containing three hex blobs and a
 * call to `slowAES.decrypt(c, 2, a, b)`. In that script mode 2 is CBC, and the
 * cookie value is the full 16 byte plaintext written as hex (padding is only
 * stripped for payloads longer than one block, which does not apply here).
 *
 * So a native client can compute the cookie itself:
 *   plaintext = AES-CBC-decrypt(ciphertext = c, key = a, iv = b)
 *   cookie    = hex(plaintext)
 *
 * Two properties of the challenge shape the design:
 *  - the cookie is bound to the User-Agent, so every request must send the same
 *    UA that solved the challenge -> [UA] is fixed for all bridge traffic.
 *  - the cookie lives 6 hours and is bound to the client IP, so it is cached per
 *    host with that TTL and refreshed on demand when a challenge is served again.
 *
 * Everything fails open: no cookie, a malformed page or a network error simply
 * yields null and the caller falls back to its previous state.
 */
@Singleton
class BridgeClient @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    private val client by lazy {
        OkHttpClient.Builder()
            .connectTimeout(12, TimeUnit.SECONDS)
            .readTimeout(25, TimeUnit.SECONDS)
            .followRedirects(true)
            .build()
    }

    // host -> (cookie, expiryEpochMs)
    private val cookies = HashMap<String, Pair<String, Long>>()

    private val prefs by lazy { context.getSharedPreferences("bridge_cookie", Context.MODE_PRIVATE) }

    /**
     * GET a text resource, transparently solving the host challenge when needed.
     * Returns the body for 2xx responses, null otherwise. Never throws.
     */
    fun getText(url: String): String? {
        for (attempt in 0..1) {
            val response = try {
                client.newCall(buildRequest(url).get().build()).execute()
            } catch (e: Exception) {
                Timber.w(e, "Bridge GET failed: $url")
                return null
            }
            val body = try {
                response.body?.string()
            } catch (e: Exception) {
                null
            }
            if (response.isSuccessful && body != null && !isChallenge(body)) {
                return body
            }
            // Likely an expired/absent cookie: solve again and retry once.
            if (attempt == 0 && solveChallenge(url)) continue
            Timber.w("Bridge GET ${response.code} for $url")
            return null
        }
        return null
    }

    /**
     * POST a JSON document. Returns true only on a 2xx response.
     * Never throws.
     */
    fun postJson(url: String, json: String): Boolean {
        for (attempt in 0..1) {
            val body = json.toRequestBody(JSON)
            val response = try {
                client.newCall(buildRequest(url).post(body).build()).execute()
            } catch (e: Exception) {
                Timber.w(e, "Bridge POST failed: $url")
                return false
            }
            val text = try {
                response.body?.string()
            } catch (e: Exception) {
                null
            }
            if (response.isSuccessful && (text == null || !isChallenge(text))) {
                return true
            }
            if (attempt == 0 && solveChallenge(url)) continue
            Timber.w("Bridge POST ${response.code} for $url")
            return false
        }
        return false
    }

    // ---------- internals ----------

    private fun buildRequest(url: String): Request.Builder {
        val builder = Request.Builder()
            .url(url)
            .header("User-Agent", UA)
        cookieFor(url)?.let { builder.header("Cookie", "__test=$it") }
        return builder
    }

    private fun origin(url: String): String? = try {
        val u = java.net.URI(url)
        val scheme = u.scheme
        val host = u.host
        if (scheme == null || host == null) null
        else "$scheme://$host" + if (u.port > 0) ":${u.port}/" else "/"
    } catch (e: Exception) {
        null
    }

    private fun hostOf(url: String): String? =
        try { java.net.URI(url).host?.lowercase() } catch (e: Exception) { null }

    private fun cookieFor(url: String): String? {
        val host = hostOf(url) ?: return null
        val cached = cookies[host]
        if (cached != null && cached.second > System.currentTimeMillis()) return cached.first
        // Restore across restarts, still honouring the expiry.
        val stored = prefs.getString("$host.cookie", null)
        val expiry = prefs.getLong("$host.expiry", 0L)
        if (stored != null && expiry > System.currentTimeMillis()) {
            cookies[host] = stored to expiry
            return stored
        }
        return null
    }

    /** Fetch the site root, derive `__test`, cache it. Returns true on success. */
    private fun solveChallenge(url: String): Boolean {
        val origin = origin(url) ?: return false
        val host = hostOf(url) ?: return false
        val page = try {
            val request = Request.Builder().url(origin).header("User-Agent", UA).build()
            client.newCall(request).execute().use { it.body?.string() }
        } catch (e: Exception) {
            Timber.w(e, "Challenge fetch failed for $origin")
            return false
        } ?: return false

        val blocks = HEX_CALL.findAll(page).map { it.groupValues[1] }.take(3).toList()
        if (blocks.size < 3) return false

        val cookie = try {
            val key = hexToBytes(blocks[0])
            val iv = hexToBytes(blocks[1])
            val cipherText = hexToBytes(blocks[2])
            val cipher = Cipher.getInstance("AES/CBC/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), IvParameterSpec(iv))
            cipher.doFinal(cipherText).joinToString("") { "%02x".format(it) }
        } catch (e: Exception) {
            Timber.w(e, "Challenge decrypt failed")
            return false
        }

        val expiry = System.currentTimeMillis() + COOKIE_TTL_MS
        cookies[host] = cookie to expiry
        prefs.edit().putString("$host.cookie", cookie).putLong("$host.expiry", expiry).apply()
        Timber.d("Bridge challenge solved for $host")
        return true
    }

    private fun isChallenge(body: String): Boolean =
        body.length < 4000 && body.contains("aes.js") && body.contains("toNumbers")

    private fun hexToBytes(hex: String): ByteArray {
        val out = ByteArray(hex.length / 2)
        for (i in out.indices) {
            out[i] = ((Character.digit(hex[i * 2], 16) shl 4) + Character.digit(hex[i * 2 + 1], 16)).toByte()
        }
        return out
    }

    companion object {
        /**
         * Fixed for all bridge traffic: the host challenge binds the cookie to the
         * User-Agent, so the solving request and the real request must match.
         */
        private const val UA =
            "Mozilla/5.0 (Linux; Android 13; Pixel 6) AppleWebKit/537.36 " +
                "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"

        private val JSON = "application/json; charset=utf-8".toMediaType()

        /** Cookie lifetime on the host is 6 hours; refresh a little early. */
        private const val COOKIE_TTL_MS = 5L * 60 * 60 * 1000

        private val HEX_CALL = Regex("""toNumbers\("([0-9a-fA-F]{32})"\)""")
    }
}
