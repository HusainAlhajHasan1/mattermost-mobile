// Copyright (c) 2015-present Mattermost, Inc. All Rights Reserved.
// See LICENSE.txt for license information.

import React, {useCallback, useEffect, useRef, useState} from 'react';
import {BackHandler, StatusBar, StyleSheet, Text, TouchableOpacity, View} from 'react-native';
import {SafeAreaView} from 'react-native-safe-area-context';

import {verifyCalculatorPin} from '@managers/calculator_lock_manager';

import {evaluateExpression, formatCalculatorResult} from './evaluate';

type Props = {
    onUnlock: () => void;
};

const MAX_EXPRESSION_LENGTH = 32;
const OPERATORS = new Set(['+', '-', '×', '÷']);
const ROWS: string[][] = [
    ['C', '⌫', '%', '÷'],
    ['7', '8', '9', '×'],
    ['4', '5', '6', '-'],
    ['1', '2', '3', '+'],
    ['0', '.', '='],
];

/**
 * A functional calculator that also acts as the Calculator Lock screen.
 * It performs standard math like any calculator; entering the 8-digit PIN
 * followed by "=" unlocks the real app. Nothing on screen hints that the
 * calculator is a lock.
 */
const CalculatorLock = ({onUnlock}: Props) => {
    const [expression, setExpression] = useState('');
    const [hasError, setHasError] = useState(false);
    const justEvaluated = useRef(false);

    // The lock must not be bypassable with the Android hardware back button.
    useEffect(() => {
        const subscription = BackHandler.addEventListener('hardwareBackPress', () => true);
        return () => subscription.remove();
    }, []);

    const appendDigit = useCallback((digit: string) => {
        setHasError(false);
        setExpression((previous) => {
            // Digits are appended verbatim (no leading-zero collapsing) so
            // PINs that start with "0" can be entered.
            const base = justEvaluated.current ? '' : previous;
            return (base + digit).slice(0, MAX_EXPRESSION_LENGTH);
        });
        justEvaluated.current = false;
    }, []);

    const appendDecimalPoint = useCallback(() => {
        setHasError(false);
        setExpression((previous) => {
            const base = justEvaluated.current ? '' : previous;
            const trailingNumber = base.match(/[0-9.]*$/)?.[0] ?? '';
            if (trailingNumber.includes('.')) {
                return base;
            }
            if (base === '' || OPERATORS.has(base.slice(-1))) {
                return `${base}0.`;
            }
            return `${base}.`;
        });
        justEvaluated.current = false;
    }, []);

    const appendOperator = useCallback((operator: string) => {
        setHasError(false);
        setExpression((previous) => {
            if (previous === '') {
                return operator === '-' ? '-' : previous;
            }
            if (OPERATORS.has(previous.slice(-1))) {
                return previous.slice(0, -1) + operator;
            }
            return previous + operator;
        });
        justEvaluated.current = false;
    }, []);

    const applyPercent = useCallback(() => {
        setHasError(false);
        setExpression((previous) => {
            const match = previous.match(/(\d*\.?\d+)$/);
            if (!match) {
                return previous;
            }
            const value = parseFloat(match[1]) / 100;
            return previous.slice(0, previous.length - match[1].length) + formatCalculatorResult(value);
        });
        justEvaluated.current = false;
    }, []);

    const clear = useCallback(() => {
        setExpression('');
        setHasError(false);
        justEvaluated.current = false;
    }, []);

    const backspace = useCallback(() => {
        setHasError(false);
        setExpression((previous) => previous.slice(0, -1));
        justEvaluated.current = false;
    }, []);

    const handleEquals = useCallback(async () => {
        const input = expression.trim();
        if (!input) {
            return;
        }

        // Secret unlock: an exact 8-digit PIN followed by "=" opens the app.
        // Any other input falls through to normal calculator behavior.
        if (await verifyCalculatorPin(input)) {
            clear();
            onUnlock();
            return;
        }

        const result = evaluateExpression(input.replace(/[+\-×÷]+$/, ''));
        if (result === null) {
            setExpression('');
            setHasError(true);
            justEvaluated.current = false;
            return;
        }

        setExpression(formatCalculatorResult(result));
        setHasError(false);
        justEvaluated.current = true;
    }, [clear, expression, onUnlock]);

    const handlePress = useCallback((button: string) => {
        if (/^[0-9]$/.test(button)) {
            appendDigit(button);
        } else if (button === '.') {
            appendDecimalPoint();
        } else if (OPERATORS.has(button)) {
            appendOperator(button);
        } else if (button === '%') {
            applyPercent();
        } else if (button === 'C') {
            clear();
        } else if (button === '⌫') {
            backspace();
        } else if (button === '=') {
            handleEquals();
        }
    }, [appendDecimalPoint, appendDigit, appendOperator, applyPercent, backspace, clear, handleEquals]);

    const display = hasError ? 'Error' : (expression || '0');

    return (
        <SafeAreaView
            style={styles.container}
            testID='calculator_lock'
        >
            <StatusBar
                barStyle='light-content'
                backgroundColor='#000000'
            />
            <View style={styles.displayContainer}>
                <Text
                    adjustsFontSizeToFit={true}
                    minimumFontScale={0.35}
                    numberOfLines={1}
                    style={styles.displayText}
                    testID='calculator_lock.display'
                >
                    {display}
                </Text>
            </View>
            <View style={styles.padContainer}>
                {ROWS.map((row) => (
                    <View
                        key={`row-${row.join('')}`}
                        style={styles.row}
                    >
                        {row.map((button) => {
                            const isOperator = OPERATORS.has(button) || button === '=';
                            const isFunction = button === 'C' || button === '⌫' || button === '%';
                            return (
                                <TouchableOpacity
                                    key={button}
                                    onPress={() => handlePress(button)}
                                    style={[
                                        styles.button,
                                        isFunction && styles.functionButton,
                                        isOperator && styles.operatorButton,
                                        button === '0' && styles.zeroButton,
                                    ]}
                                    testID={`calculator_lock.button.${button}`}
                                >
                                    <Text style={[styles.buttonText, isFunction && styles.functionButtonText]}>
                                        {button}
                                    </Text>
                                </TouchableOpacity>
                            );
                        })}
                    </View>
                ))}
            </View>
        </SafeAreaView>
    );
};

const styles = StyleSheet.create({
    container: {
        flex: 1,
        backgroundColor: '#000000',
    },
    displayContainer: {
        flex: 1,
        justifyContent: 'flex-end',
        alignItems: 'flex-end',
        paddingHorizontal: 28,
        paddingBottom: 12,
    },
    displayText: {
        color: '#FFFFFF',
        fontSize: 80,
        fontWeight: '300',
    },
    padContainer: {
        paddingHorizontal: 14,
        paddingBottom: 18,
    },
    row: {
        flexDirection: 'row',
        marginBottom: 12,
    },
    button: {
        flex: 1,
        height: 74,
        marginHorizontal: 6,
        borderRadius: 37,
        backgroundColor: '#333333',
        justifyContent: 'center',
        alignItems: 'center',
    },
    functionButton: {
        backgroundColor: '#A5A5A5',
    },
    functionButtonText: {
        color: '#000000',
    },
    operatorButton: {
        backgroundColor: '#FF9F0A',
    },
    zeroButton: {
        flex: 2,
        alignItems: 'flex-start',
        paddingLeft: 30,
    },
    buttonText: {
        color: '#FFFFFF',
        fontSize: 30,
        fontWeight: '500',
    },
});

export default CalculatorLock;
