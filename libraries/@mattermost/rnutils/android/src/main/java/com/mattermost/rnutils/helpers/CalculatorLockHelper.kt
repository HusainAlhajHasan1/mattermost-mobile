package com.mattermost.rnutils.helpers

import android.content.Context
import androidx.core.content.edit

/**
 * Persists whether Calculator Lock is enabled in SharedPreferences so the
 * native push notification handler (CustomPushNotification) can suppress
 * notifications while the app is disguised, even when the JS runtime is not
 * running. The source of truth remains the PIN entry in the keychain; the
 * JS CalculatorLockManager re-syncs this flag on every app start.
 */
object CalculatorLockHelper {
    private const val PREFS_NAME = "calculator_lock_prefs"
    private const val KEY_ENABLED = "calculator_lock_enabled"

    fun isCalculatorLockEnabled(context: Context): Boolean {
        return try {
            context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                    .getBoolean(KEY_ENABLED, false)
        } catch (e: Exception) {
            e.printStackTrace()
            false
        }
    }

    fun setCalculatorLockEnabled(context: Context, enabled: Boolean) {
        try {
            context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit {
                putBoolean(KEY_ENABLED, enabled)
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }
}
