package fulguris.js

import com.anthonycr.mezzanine.FileStream

/**
 * DOM level ad sweeper. See src/main/js/AdCleaner.js for the implementation.
 * Configuration is passed by injecting `window.__ADC__ = {...}` right before this script.
 */
@FileStream("src/main/js/AdCleaner.js")
interface AdCleaner {

    fun provideJs(): String

}
