// Copyright (c) 2015-present Mattermost, Inc. All Rights Reserved.
// See LICENSE.txt for license information.

type Operator = '+' | '-' | '*' | '/';
type Token = number | Operator;

const OPERATORS = new Set<string>(['+', '-', '*', '/']);

/**
 * Safely evaluates a basic arithmetic expression ("12×3+4÷2") without
 * eval/Function. Supports +, -, ×, ÷, decimal numbers and unary minus with
 * standard operator precedence. Returns null for invalid expressions and
 * non-finite results (e.g. division by zero).
 */
export function evaluateExpression(expression: string): number | null {
    const normalized = expression.replace(/×/g, '*').replace(/÷/g, '/').trim();
    if (!normalized || !/^[0-9+\-*/. ]+$/.test(normalized)) {
        return null;
    }

    const tokens = tokenize(normalized);
    if (!tokens) {
        return null;
    }

    // First pass: multiplication and division.
    const terms: Token[] = [];
    for (const token of tokens) {
        const previous = terms[terms.length - 1];
        if (typeof token === 'number' && (previous === '*' || previous === '/')) {
            terms.pop();
            const left = terms.pop();
            if (typeof left !== 'number') {
                return null;
            }
            if (previous === '/' && token === 0) {
                return null;
            }
            terms.push(previous === '*' ? left * token : left / token);
        } else {
            terms.push(token);
        }
    }

    // Second pass: addition and subtraction.
    let result = terms[0];
    if (typeof result !== 'number') {
        return null;
    }
    for (let i = 1; i < terms.length; i += 2) {
        const operator = terms[i];
        const operand = terms[i + 1];
        if ((operator !== '+' && operator !== '-') || typeof operand !== 'number') {
            return null;
        }
        result = operator === '+' ? result + operand : result - operand;
    }

    return Number.isFinite(result) ? result : null;
}

function tokenize(expression: string): Token[] | null {
    const tokens: Token[] = [];
    let i = 0;

    while (i < expression.length) {
        const char = expression[i];

        if (char === ' ') {
            i++;
            continue;
        }

        const previous = tokens[tokens.length - 1];
        const expectOperand = tokens.length === 0 || typeof previous === 'string';

        // A '-' where an operand is expected is a unary minus.
        if (OPERATORS.has(char) && !(char === '-' && expectOperand)) {
            if (expectOperand) {
                return null;
            }
            tokens.push(char as Operator);
            i++;
            continue;
        }

        let sign = 1;
        if (char === '-') {
            sign = -1;
            i++;
        }

        let numberText = '';
        let seenDot = false;
        while (i < expression.length && (/[0-9]/.test(expression[i]) || expression[i] === '.')) {
            if (expression[i] === '.') {
                if (seenDot) {
                    return null;
                }
                seenDot = true;
            }
            numberText += expression[i];
            i++;
        }

        if (numberText === '' || numberText === '.') {
            return null;
        }

        if (!expectOperand) {
            return null;
        }
        tokens.push(sign * parseFloat(numberText));
    }

    if (tokens.length === 0 || typeof tokens[tokens.length - 1] === 'string') {
        return null;
    }

    return tokens;
}

/**
 * Formats an evaluation result for the calculator display, hiding floating
 * point noise (0.1 + 0.2 = 0.3) and switching to exponential notation for
 * very large or very small magnitudes.
 */
export function formatCalculatorResult(value: number): string {
    const rounded = Number(value.toFixed(8));
    if (rounded !== 0 && (Math.abs(rounded) >= 1e12 || Math.abs(rounded) < 1e-9)) {
        return rounded.toExponential(6).replace(/(\.\d*?)0+e/, '$1e').replace(/\.e/, 'e');
    }
    return String(rounded);
}
