package fulguris.adrules

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import fulguris.settings.preferences.UserPreferences
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.GlobalScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import timber.log.Timber
import java.io.File
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Loads, caches and serves the AI community site rules (site-rules.json)
 * distributed through the PHP bridge.
 *
 * Contract:
 *  - [ruleFor] never blocks: if rules are not loaded yet it returns null (fail-open,
 *    no ad protection but no broken browsing either).
 *  - [updateAsync] fetches the rules document off the main thread, supports
 *    ETag/If-None-Match to keep mobile data usage minimal, and persists the last
 *    good document to cache so it survives restarts and offline starts.
 */
@Singleton
class SiteRulesManager @Inject constructor(
    @ApplicationContext private val context: Context,
    private val userPreferences: UserPreferences,
) {

    @Volatile private var index: SiteRulesIndex? = null
    @Volatile private var lastFetchAttempt: Long = 0

    private val fetchMutex = Mutex()

    // Persisted ETag for conditional requests.
    private val prefs by lazy { context.getSharedPreferences("site_rules", Context.MODE_PRIVATE) }

    private val client by lazy {
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
            .build()
    }

    private val cacheFile: File
        get() = File(context.filesDir, "site_rules.json")

    init {
        // Load the last good rules immediately (background thread, non-blocking for startup).
        GlobalScope.launch(Dispatchers.IO) {
            loadFromCache()
            // Then refresh from the bridge if enabled.
            if (userPreferences.siteRulesEnabled) {
                updateAsync(force = false)
            }
        }
    }

    /**
     * Rule for the given page host, or null when unknown/disabled.
     * Never blocks, never throws.
     */
    fun ruleFor(host: String?): SiteRule? {
        if (host.isNullOrBlank()) return null
        if (!userPreferences.siteRulesEnabled) return null
        return index?.ruleFor(host)
    }

    /**
     * Fetch the rules document from [UserPreferences.siteRulesUrl].
     * Consecutive calls are at least [MIN_FETCH_INTERVAL_MS] apart unless [force].
     * Returns true when the index changed.
     */
    suspend fun updateAsync(force: Boolean): Boolean = fetchMutex.withLock {
        val now = System.currentTimeMillis()
        if (!force && now - lastFetchAttempt < MIN_FETCH_INTERVAL_MS) return@withLock false
        lastFetchAttempt = now

        val url = userPreferences.siteRulesUrl
        if (url.isBlank() || !url.startsWith("http")) return@withLock false

        return@withLock try {
            val builder = Request.Builder().url(url)
            prefs.getString(KEY_ETAG, null)?.let { builder.header("If-None-Match", it) }
            prefs.getString(KEY_MODIFIED, null)?.let { builder.header("If-Modified-Since", it) }

            client.newCall(builder.build()).execute().use { response ->
                when {
                    response.code == 304 -> {
                        Timber.d("Site rules unchanged (304)")
                        false
                    }
                    response.isSuccessful -> {
                        val body = response.body?.string() ?: return@withLock false
                        val parsed = SiteRulesIndex.fromJson(JSONObject(body))
                        index = parsed
                        // Persist the good document.
                        runCatching { cacheFile.writeText(body) }
                        response.header("ETag")?.let { prefs.edit().putString(KEY_ETAG, it).apply() }
                        response.header("Last-Modified")?.let { prefs.edit().putString(KEY_MODIFIED, it).apply() }
                        Timber.d("Site rules updated: ${parsed.sites.size} sites (v${parsed.version})")
                        true
                    }
                    else -> {
                        Timber.w("Site rules fetch failed: HTTP ${response.code}")
                        false
                    }
                }
            }
        } catch (e: Exception) {
            // Network unavailable, malformed document, ... fail-open, keep old rules.
            Timber.w(e, "Site rules fetch error")
            false
        }
    }

    private fun loadFromCache() {
        runCatching {
            if (cacheFile.exists()) {
                index = SiteRulesIndex.fromJson(JSONObject(cacheFile.readText()))
                Timber.d("Site rules loaded from cache: ${index?.sites?.size ?: 0} sites")
            }
        }
    }

    /** Human readable count for settings display. */
    fun sitesCount(): Int = index?.sites?.size ?: 0

    companion object {
        private const val KEY_ETAG = "etag"
        private const val KEY_MODIFIED = "modified"
        private const val MIN_FETCH_INTERVAL_MS = 60_000L // At most one attempt per minute
    }
}
