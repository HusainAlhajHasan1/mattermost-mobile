// Copyright (c) 2015-present Mattermost, Inc. All Rights Reserved.
// See LICENSE.txt for license information.

// Keychain service under which the Calculator Lock PIN (hash) is stored. This
// value must never look like a server URL: on Android, react-native-keychain
// generic-password services are enumerated as candidate server URLs by
// getAllKeychainServerUrls in app/init/credentials.ts, which explicitly
// filters this service out.
export const CALCULATOR_LOCK_KEYCHAIN_SERVICE = 'mattermost_calculator_lock_pin';

// Keychain account (username) for the Calculator Lock PIN entry.
export const CALCULATOR_LOCK_KEYCHAIN_ACCOUNT = 'calc_lock';

// Name passed to the native icon switcher to enable the disguised icon.
// On Android it maps to the .MainActivityCalculator activity-alias; on iOS it
// maps to the CalculatorIcon alternate app icon asset.
export const CALCULATOR_LOCK_ICON_NAME = 'Calculator';

// Icon name used to restore the default Mattermost icon (empty = default).
export const CALCULATOR_LOCK_DEFAULT_ICON_NAME = '';

// The Calculator Lock PIN is exactly 8 digits.
export const CALCULATOR_LOCK_PIN_LENGTH = 8;
export const CALCULATOR_LOCK_PIN_REGEX = /^\d{8}$/;
