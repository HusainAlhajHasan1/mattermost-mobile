// Copyright (c) 2015-present Mattermost, Inc. All Rights Reserved.
// See LICENSE.txt for license information.

import RNUtils from '@mattermost/rnutils';
import {sha256} from 'js-sha256';
import {AppState, type AppStateStatus, type NativeEventSubscription} from 'react-native';
import * as Keychain from 'react-native-keychain';

import {
    CALCULATOR_LOCK_DEFAULT_ICON_NAME,
    CALCULATOR_LOCK_ICON_NAME,
    CALCULATOR_LOCK_KEYCHAIN_ACCOUNT,
    CALCULATOR_LOCK_KEYCHAIN_SERVICE,
    CALCULATOR_LOCK_PIN_REGEX,
} from '@constants/calculator_lock';
import CalculatorLockStore from '@store/calculator_lock_store';
import {getFullErrorMessage} from '@utils/errors';
import {logDebug, logWarning} from '@utils/log';

// Changing the app icon on iOS presents a system alert, which moves the app
// to the 'inactive' state. Ignore lock requests triggered by brief
// 'inactive' transitions right after the lock is toggled so the user is not
// locked out of the session they just configured.
const ICON_CHANGE_GRACE_MS = 3000;

/**
 * Calculator Lock disguises the app as a calculator:
 * - the launcher icon/name become "Calculator",
 * - on cold start and when returning from the background a fully functional
 *   calculator is shown instead of the app, and
 * - entering the 8-digit PIN followed by "=" unlocks the real app.
 *
 * While enabled, no chat content may leak through notifications: delivered
 * notifications are cleared, the JS push handlers suppress presentation, and
 * the native push handlers (Android CustomPushNotification, iOS
 * NotificationService extension) check the native flag synced via
 * RNUtils.setCalculatorLockEnabled so notifications stay hidden even when
 * the app process is not running.
 */
class CalculatorLockManagerSingleton {
    private enabledCache = false;
    private initialized = false;
    private appStateSubscription?: NativeEventSubscription;
    private lastToggleAt = 0;

    init = async () => {
        if (this.initialized) {
            return;
        }
        this.initialized = true;

        logDebug('CalculatorLockManager: Initializing');
        this.appStateSubscription = AppState.addEventListener('change', this.onAppStateChange);

        const enabled = await this.refreshEnabled();

        // Heal any drift between the keychain (source of truth) and the
        // native flag read by the platform push notification handlers.
        this.syncNativeFlag(enabled);

        if (enabled) {
            this.lockNow();
        }
    };

    cleanup = () => {
        this.appStateSubscription?.remove();
        this.appStateSubscription = undefined;
        this.initialized = false;
    };

    refreshEnabled = async (): Promise<boolean> => {
        try {
            const credentials = await Keychain.getGenericPassword({service: CALCULATOR_LOCK_KEYCHAIN_SERVICE});
            this.enabledCache = Boolean(credentials && credentials.password);
        } catch (error) {
            logWarning('CalculatorLockManager: could not read lock state', getFullErrorMessage(error));
            this.enabledCache = false;
        }
        return this.enabledCache;
    };

    isEnabledSync = (): boolean => {
        return this.enabledCache;
    };

    isLockedSync = (): boolean => {
        return CalculatorLockStore.isLocked();
    };

    enableLock = async (pin: string): Promise<void> => {
        if (!CALCULATOR_LOCK_PIN_REGEX.test(pin)) {
            throw new Error('PIN must be exactly 8 digits.');
        }

        // Only a hash of the PIN is stored; the PIN itself never persists.
        const stored = await Keychain.setGenericPassword(CALCULATOR_LOCK_KEYCHAIN_ACCOUNT, sha256(pin), {
            service: CALCULATOR_LOCK_KEYCHAIN_SERVICE,
            accessible: Keychain.ACCESSIBLE.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
            securityLevel: Keychain.SECURITY_LEVEL.SECURE_SOFTWARE,
        });
        if (stored === false) {
            throw new Error('Failed to store the Calculator Lock PIN.');
        }

        this.enabledCache = true;
        this.lastToggleAt = Date.now();
        this.syncNativeFlag(true);
        this.clearDeliveredNotifications();
        await this.changeIcon(CALCULATOR_LOCK_ICON_NAME);

        // The session in which the lock was enabled stays unlocked; the
        // calculator appears on the next cold start or backgrounding.
        CalculatorLockStore.setLocked(false);
    };

    disableLock = async (): Promise<void> => {
        await Keychain.resetGenericPassword({service: CALCULATOR_LOCK_KEYCHAIN_SERVICE});

        this.enabledCache = false;
        this.lastToggleAt = Date.now();
        this.syncNativeFlag(false);
        CalculatorLockStore.setLocked(false);
        await this.changeIcon(CALCULATOR_LOCK_DEFAULT_ICON_NAME);
    };

    verifyPin = async (input: string): Promise<boolean> => {
        if (!CALCULATOR_LOCK_PIN_REGEX.test(input)) {
            return false;
        }

        try {
            const credentials = await Keychain.getGenericPassword({service: CALCULATOR_LOCK_KEYCHAIN_SERVICE});
            if (!credentials) {
                return false;
            }
            return credentials.password === sha256(input);
        } catch (error) {
            logWarning('CalculatorLockManager: could not verify PIN', getFullErrorMessage(error));
            return false;
        }
    };

    lockNow = () => {
        if (!this.enabledCache) {
            return;
        }
        this.clearDeliveredNotifications();
        CalculatorLockStore.setLocked(true);
    };

    unlock = () => {
        CalculatorLockStore.setLocked(false);
    };

    clearDeliveredNotifications = () => {
        try {
            RNUtils.removeAllDeliveredNotifications?.();
        } catch (error) {
            logWarning('CalculatorLockManager: could not clear notifications', getFullErrorMessage(error));
        }
    };

    private syncNativeFlag = (enabled: boolean) => {
        try {
            RNUtils.setCalculatorLockEnabled?.(enabled);
        } catch (error) {
            logWarning('CalculatorLockManager: could not sync native lock flag', getFullErrorMessage(error));
        }
    };

    private changeIcon = async (iconName: string): Promise<boolean> => {
        try {
            return await RNUtils.changeAppIcon?.(iconName) ?? false;
        } catch (error) {
            logWarning('CalculatorLockManager: could not change app icon', getFullErrorMessage(error));
            return false;
        }
    };

    private onAppStateChange = (nextState: AppStateStatus) => {
        if (!this.enabledCache) {
            return;
        }

        if (nextState === 'background') {
            this.lockNow();
        } else if (nextState === 'inactive') {
            if (Date.now() - this.lastToggleAt > ICON_CHANGE_GRACE_MS) {
                this.lockNow();
            }
        } else if (nextState === 'active' && this.isLockedSync()) {
            // Sweep anything that was delivered while we were away before
            // the user gets a chance to unlock.
            this.clearDeliveredNotifications();
        }
    };
}

const CalculatorLockManager = new CalculatorLockManagerSingleton();
export default CalculatorLockManager;

// Named helpers mirroring the manager for ergonomic imports in hot paths
// such as the push notification handlers.
export const isCalculatorLockEnabled = (): Promise<boolean> => CalculatorLockManager.refreshEnabled();
export const isCalculatorLockEnabledSync = (): boolean => CalculatorLockManager.isEnabledSync();
export const isCalculatorLockedSync = (): boolean => CalculatorLockManager.isLockedSync();
export const enableCalculatorLock = (pin: string): Promise<void> => CalculatorLockManager.enableLock(pin);
export const disableCalculatorLock = (): Promise<void> => CalculatorLockManager.disableLock();
export const verifyCalculatorPin = (input: string): Promise<boolean> => CalculatorLockManager.verifyPin(input);
export const clearDeliveredNotificationsIfLocked = (): void => {
    if (CalculatorLockManager.isEnabledSync()) {
        CalculatorLockManager.clearDeliveredNotifications();
    }
};
