// Copyright (c) 2015-present Mattermost, Inc. All Rights Reserved.
// See LICENSE.txt for license information.

import React, {useCallback, useState} from 'react';
import {defineMessage, useIntl} from 'react-intl';
import {Alert, Modal, Text, TextInput, TouchableOpacity, View} from 'react-native';

import SettingBlock from '@components/settings/block';
import SettingOption from '@components/settings/option';
import SettingSeparator from '@components/settings/separator';
import {CALCULATOR_LOCK_PIN_LENGTH, CALCULATOR_LOCK_PIN_REGEX} from '@constants/calculator_lock';
import {useTheme} from '@context/theme';
import useDidMount from '@hooks/did_mount';
import {
    disableCalculatorLock,
    enableCalculatorLock,
    isCalculatorLockEnabled,
    verifyCalculatorPin,
} from '@managers/calculator_lock_manager';
import {changeOpacity, makeStyleSheetFromTheme} from '@utils/theme';
import {typography} from '@utils/typography';

const description = defineMessage({
    id: 'settings.advanced.calculator_lock.description',
    defaultMessage: 'Disguise the app as a calculator protected by an 8-digit PIN. The app icon changes to Calculator, the app opens as a working calculator, and notifications are hidden. Enter your PIN followed by "=" on the calculator to unlock.',
});

const setPinTitle = defineMessage({
    id: 'settings.advanced.calculator_lock.set_pin.title',
    defaultMessage: 'Set 8-Digit PIN',
});
const verifyPinTitle = defineMessage({
    id: 'settings.advanced.calculator_lock.verify_pin.title',
    defaultMessage: 'Enter Current PIN',
});
const setPinDescription = defineMessage({
    id: 'settings.advanced.calculator_lock.set_pin.description',
    defaultMessage: 'Enter an 8-digit numeric PIN. To unlock the app, enter this PIN on the calculator followed by "=".',
});
const verifyPinDescription = defineMessage({
    id: 'settings.advanced.calculator_lock.verify_pin.description',
    defaultMessage: 'Enter your current 8-digit PIN to disable Calculator Lock.',
});
const enableLabel = defineMessage({
    id: 'settings.advanced.calculator_lock.enable',
    defaultMessage: 'Enable Lock',
});
const disableLabel = defineMessage({
    id: 'settings.advanced.calculator_lock.disable',
    defaultMessage: 'Disable Lock',
});

const getStyleSheet = makeStyleSheetFromTheme((theme: Theme) => ({
    modalBackdrop: {
        flex: 1,
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
        justifyContent: 'center',
        padding: 24,
    },
    modalBox: {
        backgroundColor: theme.centerChannelBg,
        borderRadius: 12,
        padding: 20,
    },
    modalTitle: {
        color: theme.centerChannelColor,
        ...typography('Heading', 300, 'SemiBold'),
        marginBottom: 8,
    },
    modalDescription: {
        color: changeOpacity(theme.centerChannelColor, 0.64),
        ...typography('Body', 100),
        marginBottom: 16,
    },
    input: {
        borderWidth: 1,
        borderColor: changeOpacity(theme.centerChannelColor, 0.24),
        borderRadius: 8,
        color: theme.centerChannelColor,
        ...typography('Body', 200),
        letterSpacing: 4,
        marginBottom: 12,
        paddingHorizontal: 12,
        paddingVertical: 10,
        textAlign: 'center',
    },
    modalActions: {
        flexDirection: 'row',
        justifyContent: 'flex-end',
        gap: 24,
        marginTop: 4,
    },
    cancelText: {
        color: changeOpacity(theme.centerChannelColor, 0.64),
        ...typography('Body', 200),
    },
    confirmText: {
        color: theme.buttonBg,
        ...typography('Body', 200, 'SemiBold'),
    },
}));

type DialogMode = 'enable' | 'disable';

const CalculatorLockSetting = () => {
    const intl = useIntl();
    const theme = useTheme();
    const styles = getStyleSheet(theme);

    const [enabled, setEnabled] = useState(false);
    const [dialogMode, setDialogMode] = useState<DialogMode | null>(null);
    const [pin, setPin] = useState('');
    const [confirmPin, setConfirmPin] = useState('');
    const [busy, setBusy] = useState(false);

    useDidMount(() => {
        isCalculatorLockEnabled().then(setEnabled).catch(() => setEnabled(false));
    });

    const closeDialog = useCallback(() => {
        setDialogMode(null);
        setPin('');
        setConfirmPin('');
        setBusy(false);
    }, []);

    const onToggle = useCallback((value: string | boolean) => {
        setPin('');
        setConfirmPin('');
        setDialogMode(Boolean(value) ? 'enable' : 'disable');
    }, []);

    const handleEnable = useCallback(async () => {
        if (!CALCULATOR_LOCK_PIN_REGEX.test(pin)) {
            Alert.alert(
                intl.formatMessage({id: 'settings.advanced.calculator_lock.invalid_pin.title', defaultMessage: 'Invalid PIN'}),
                intl.formatMessage({id: 'settings.advanced.calculator_lock.invalid_pin.message', defaultMessage: 'Please enter an exact 8-digit numeric PIN.'}),
            );
            return;
        }

        if (pin !== confirmPin) {
            Alert.alert(
                intl.formatMessage({id: 'settings.advanced.calculator_lock.invalid_pin.title', defaultMessage: 'Invalid PIN'}),
                intl.formatMessage({id: 'settings.advanced.calculator_lock.pin_mismatch', defaultMessage: 'The PINs you entered do not match. Please try again.'}),
            );
            return;
        }

        setBusy(true);
        try {
            await enableCalculatorLock(pin);
            setEnabled(true);
            closeDialog();
            Alert.alert(
                intl.formatMessage({id: 'settings.advanced.calculator_lock.enabled.title', defaultMessage: 'Calculator Lock Enabled'}),
                intl.formatMessage({id: 'settings.advanced.calculator_lock.enabled.message', defaultMessage: 'Your app icon is now disguised as Calculator and notifications are hidden. Enter your 8-digit PIN followed by "=" to unlock. If you forget your PIN, you will not be able to unlock the app.'}),
            );
        } catch {
            setBusy(false);
            Alert.alert(intl.formatMessage({id: 'settings.advanced.calculator_lock.error', defaultMessage: 'Something went wrong. Please try again.'}));
        }
    }, [closeDialog, confirmPin, intl, pin]);

    const handleDisable = useCallback(async () => {
        if (!CALCULATOR_LOCK_PIN_REGEX.test(pin)) {
            Alert.alert(
                intl.formatMessage({id: 'settings.advanced.calculator_lock.invalid_pin.title', defaultMessage: 'Invalid PIN'}),
                intl.formatMessage({id: 'settings.advanced.calculator_lock.invalid_pin.message', defaultMessage: 'Please enter an exact 8-digit numeric PIN.'}),
            );
            return;
        }

        setBusy(true);
        try {
            const verified = await verifyCalculatorPin(pin);
            if (!verified) {
                setBusy(false);
                Alert.alert(
                    intl.formatMessage({id: 'settings.advanced.calculator_lock.invalid_pin.title', defaultMessage: 'Invalid PIN'}),
                    intl.formatMessage({id: 'settings.advanced.calculator_lock.wrong_pin', defaultMessage: 'The PIN you entered is incorrect.'}),
                );
                return;
            }

            await disableCalculatorLock();
            setEnabled(false);
            closeDialog();
            Alert.alert(
                intl.formatMessage({id: 'settings.advanced.calculator_lock.disabled.title', defaultMessage: 'Calculator Lock Disabled'}),
                intl.formatMessage({id: 'settings.advanced.calculator_lock.disabled.message', defaultMessage: 'The default app icon and notifications have been restored.'}),
            );
        } catch {
            setBusy(false);
            Alert.alert(intl.formatMessage({id: 'settings.advanced.calculator_lock.error', defaultMessage: 'Something went wrong. Please try again.'}));
        }
    }, [closeDialog, intl, pin]);

    const sanitizePin = useCallback((text: string, setter: (value: string) => void) => {
        setter(text.replace(/[^0-9]/g, '').slice(0, CALCULATOR_LOCK_PIN_LENGTH));
    }, []);

    const isEnableDialog = dialogMode === 'enable';
    const placeholderColor = changeOpacity(theme.centerChannelColor, 0.4);

    const dialogTitle = intl.formatMessage(isEnableDialog ? setPinTitle : verifyPinTitle);
    const dialogDescription = intl.formatMessage(isEnableDialog ? setPinDescription : verifyPinDescription);
    const confirmLabel = intl.formatMessage(isEnableDialog ? enableLabel : disableLabel);

    return (
        <SettingBlock footerText={description}>
            <SettingOption
                action={onToggle}
                icon='lock-outline'
                label={intl.formatMessage({id: 'settings.advanced.calculator_lock.title', defaultMessage: 'Calculator App Lock'})}
                selected={enabled}
                testID='advanced_settings.calculator_lock.toggle'
                type='toggle'
            />
            <SettingSeparator/>
            <Modal
                animationType='fade'
                onRequestClose={closeDialog}
                transparent={true}
                visible={dialogMode !== null}
            >
                <View style={styles.modalBackdrop}>
                    <View style={styles.modalBox}>
                        <Text style={styles.modalTitle}>
                            {dialogTitle}
                        </Text>
                        <Text style={styles.modalDescription}>
                            {dialogDescription}
                        </Text>
                        <TextInput
                            autoFocus={true}
                            editable={!busy}
                            keyboardType='number-pad'
                            maxLength={CALCULATOR_LOCK_PIN_LENGTH}
                            onChangeText={(text) => sanitizePin(text, setPin)}
                            placeholder={intl.formatMessage({id: 'settings.advanced.calculator_lock.pin.placeholder', defaultMessage: 'Enter 8 digits'})}
                            placeholderTextColor={placeholderColor}
                            secureTextEntry={true}
                            style={styles.input}
                            testID='advanced_settings.calculator_lock.pin.input'
                            value={pin}
                        />
                        {isEnableDialog && (
                            <TextInput
                                editable={!busy}
                                keyboardType='number-pad'
                                maxLength={CALCULATOR_LOCK_PIN_LENGTH}
                                onChangeText={(text) => sanitizePin(text, setConfirmPin)}
                                placeholder={intl.formatMessage({id: 'settings.advanced.calculator_lock.pin_confirm.placeholder', defaultMessage: 'Confirm PIN'})}
                                placeholderTextColor={placeholderColor}
                                secureTextEntry={true}
                                style={styles.input}
                                testID='advanced_settings.calculator_lock.pin_confirm.input'
                                value={confirmPin}
                            />
                        )}
                        <View style={styles.modalActions}>
                            <TouchableOpacity
                                disabled={busy}
                                onPress={closeDialog}
                                testID='advanced_settings.calculator_lock.cancel.button'
                            >
                                <Text style={styles.cancelText}>
                                    {intl.formatMessage({id: 'settings.advanced.cancel', defaultMessage: 'Cancel'})}
                                </Text>
                            </TouchableOpacity>
                            <TouchableOpacity
                                disabled={busy}
                                onPress={isEnableDialog ? handleEnable : handleDisable}
                                testID='advanced_settings.calculator_lock.confirm.button'
                            >
                                <Text style={styles.confirmText}>
                                    {confirmLabel}
                                </Text>
                            </TouchableOpacity>
                        </View>
                    </View>
                </View>
            </Modal>
        </SettingBlock>
    );
};

export default CalculatorLockSetting;
