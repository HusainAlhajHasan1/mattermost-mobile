// Copyright (c) 2015-present Mattermost, Inc. All Rights Reserved.
// See LICENSE.txt for license information.

import {sha256} from 'js-sha256';

jest.mock('react-native-keychain', () => ({
    ACCESSIBLE: {WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'AccessibleWhenUnlockedThisDeviceOnly'},
    SECURITY_LEVEL: {SECURE_SOFTWARE: 'SECURE_SOFTWARE'},
    getGenericPassword: jest.fn(),
    setGenericPassword: jest.fn(),
    resetGenericPassword: jest.fn(),
}));

describe('CalculatorLockManager', () => {
    let CalculatorLockManager: typeof import('./calculator_lock_manager').default;
    let CalculatorLockStore: typeof import('@store/calculator_lock_store').default;
    let Keychain: typeof import('react-native-keychain');
    let RNUtils: typeof import('@mattermost/rnutils').default;

    const pin = '08152424';

    const storedPinCredentials = () => ({
        username: 'calc_lock',
        password: sha256(pin),
        service: 'mattermost_calculator_lock_pin',
        storage: 'keychain',
    } as never);

    beforeEach(() => {
        // Fresh singleton (and fresh mock instances) per test.
        jest.resetModules();
        Keychain = require('react-native-keychain');
        RNUtils = require('@mattermost/rnutils').default;
        CalculatorLockManager = require('./calculator_lock_manager').default;
        CalculatorLockStore = require('@store/calculator_lock_store').default;
    });

    describe('enableLock', () => {
        it('should reject a PIN that is not exactly 8 digits', async () => {
            await expect(CalculatorLockManager.enableLock('123')).rejects.toThrow('8 digits');
            await expect(CalculatorLockManager.enableLock('123456789')).rejects.toThrow('8 digits');
            await expect(CalculatorLockManager.enableLock('1234abcd')).rejects.toThrow('8 digits');
            expect(Keychain.setGenericPassword).not.toHaveBeenCalled();
        });

        it('should store only a hash of the PIN and disguise the app', async () => {
            jest.mocked(Keychain.setGenericPassword).mockResolvedValue(true as never);

            await CalculatorLockManager.enableLock(pin);

            expect(Keychain.setGenericPassword).toHaveBeenCalledTimes(1);
            const [account, storedValue, options] = jest.mocked(Keychain.setGenericPassword).mock.calls[0];
            expect(account).toBe('calc_lock');
            expect(storedValue).toBe(sha256(pin));
            expect(storedValue).not.toBe(pin);
            expect(options?.service).toBe('mattermost_calculator_lock_pin');

            expect(CalculatorLockManager.isEnabledSync()).toBe(true);
            expect(RNUtils.setCalculatorLockEnabled).toHaveBeenCalledWith(true);
            expect(RNUtils.changeAppIcon).toHaveBeenCalledWith('Calculator');
            expect(RNUtils.removeAllDeliveredNotifications).toHaveBeenCalled();

            // Enabling the lock must not interrupt the current session.
            expect(CalculatorLockStore.isLocked()).toBe(false);
        });
    });

    describe('verifyPin', () => {
        beforeEach(() => {
            jest.mocked(Keychain.getGenericPassword).mockResolvedValue(storedPinCredentials());
        });

        it('should accept the correct PIN', async () => {
            await expect(CalculatorLockManager.verifyPin(pin)).resolves.toBe(true);
        });

        it('should reject a wrong or malformed PIN', async () => {
            await expect(CalculatorLockManager.verifyPin('00000000')).resolves.toBe(false);
            await expect(CalculatorLockManager.verifyPin('123')).resolves.toBe(false);
            await expect(CalculatorLockManager.verifyPin(pin + ' ')).resolves.toBe(false);
        });

        it('should return false when no PIN is stored', async () => {
            jest.mocked(Keychain.getGenericPassword).mockResolvedValue(false as never);
            await expect(CalculatorLockManager.verifyPin(pin)).resolves.toBe(false);
        });
    });

    describe('disableLock', () => {
        it('should clear the PIN, the native flag and restore the default icon', async () => {
            jest.mocked(Keychain.setGenericPassword).mockResolvedValue(true as never);
            jest.mocked(Keychain.resetGenericPassword).mockResolvedValue(true as never);

            await CalculatorLockManager.enableLock(pin);
            await CalculatorLockManager.disableLock();

            expect(Keychain.resetGenericPassword).toHaveBeenCalledWith({service: 'mattermost_calculator_lock_pin'});
            expect(CalculatorLockManager.isEnabledSync()).toBe(false);
            expect(RNUtils.setCalculatorLockEnabled).toHaveBeenLastCalledWith(false);
            expect(RNUtils.changeAppIcon).toHaveBeenLastCalledWith('');
            expect(CalculatorLockStore.isLocked()).toBe(false);
        });
    });

    describe('lockNow and refreshEnabled', () => {
        it('should lock and clear notifications only when enabled', async () => {
            jest.mocked(Keychain.getGenericPassword).mockResolvedValue(false as never);
            await expect(CalculatorLockManager.refreshEnabled()).resolves.toBe(false);

            CalculatorLockManager.lockNow();
            expect(CalculatorLockStore.isLocked()).toBe(false);

            jest.mocked(Keychain.getGenericPassword).mockResolvedValue(storedPinCredentials());
            await expect(CalculatorLockManager.refreshEnabled()).resolves.toBe(true);
            expect(CalculatorLockManager.isEnabledSync()).toBe(true);

            CalculatorLockManager.lockNow();
            expect(CalculatorLockStore.isLocked()).toBe(true);
            expect(RNUtils.removeAllDeliveredNotifications).toHaveBeenCalled();

            CalculatorLockManager.unlock();
            expect(CalculatorLockStore.isLocked()).toBe(false);
        });
    });

    describe('init', () => {
        it('should engage the lock on cold start when a PIN is set', async () => {
            jest.mocked(Keychain.getGenericPassword).mockResolvedValue(storedPinCredentials());

            await CalculatorLockManager.init();

            expect(CalculatorLockStore.isLocked()).toBe(true);
            expect(RNUtils.setCalculatorLockEnabled).toHaveBeenCalledWith(true);
            expect(RNUtils.removeAllDeliveredNotifications).toHaveBeenCalled();

            CalculatorLockManager.cleanup();
        });

        it('should stay unlocked on cold start when no PIN is set', async () => {
            jest.mocked(Keychain.getGenericPassword).mockResolvedValue(false as never);

            await CalculatorLockManager.init();

            expect(CalculatorLockStore.isLocked()).toBe(false);
            expect(RNUtils.setCalculatorLockEnabled).toHaveBeenCalledWith(false);

            CalculatorLockManager.cleanup();
        });
    });
});
