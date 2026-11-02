// Copyright (c) 2015-present Mattermost, Inc. All Rights Reserved.
// See LICENSE.txt for license information.

import {evaluateExpression, formatCalculatorResult} from './evaluate';

describe('evaluateExpression', () => {
    it('should evaluate basic arithmetic with operator precedence', () => {
        expect(evaluateExpression('2+3')).toBe(5);
        expect(evaluateExpression('2+3×4')).toBe(14);
        expect(evaluateExpression('8÷2')).toBe(4);
        expect(evaluateExpression('10-2×3')).toBe(4);
        expect(evaluateExpression('1.5×2')).toBe(3);
    });

    it('should handle unary minus', () => {
        expect(evaluateExpression('-5+3')).toBe(-2);
        expect(evaluateExpression('5×-2')).toBe(-10);
        expect(evaluateExpression('5--3')).toBe(8);
    });

    it('should evaluate a plain number, including leading zeroes', () => {
        expect(evaluateExpression('42')).toBe(42);
        expect(evaluateExpression('007')).toBe(7);
        expect(evaluateExpression('08152424')).toBe(8152424);
    });

    it('should return null for invalid expressions', () => {
        expect(evaluateExpression('')).toBeNull();
        expect(evaluateExpression('5+')).toBeNull();
        expect(evaluateExpression('3..2')).toBeNull();
        expect(evaluateExpression('1÷0')).toBeNull();
        expect(evaluateExpression('abc')).toBeNull();
        expect(evaluateExpression('2++3')).toBeNull();
    });
});

describe('formatCalculatorResult', () => {
    it('should hide floating point noise', () => {
        expect(formatCalculatorResult(0.1 + 0.2)).toBe('0.3');
        expect(formatCalculatorResult(1 / 3)).toBe('0.33333333');
        expect(formatCalculatorResult(-0)).toBe('0');
    });

    it('should use exponential notation for very large results', () => {
        expect(formatCalculatorResult(1e13)).toBe('1e+13');
    });
});
