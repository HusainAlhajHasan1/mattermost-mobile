// Copyright (c) 2015-present Mattermost, Inc. All Rights Reserved.
// See LICENSE.txt for license information.

import {BehaviorSubject} from 'rxjs';

type CalculatorLockState = {
    isLocked: boolean;
};

class CalculatorLockStoreSingleton {
    private subject = new BehaviorSubject<CalculatorLockState>({
        isLocked: false,
    });

    setLocked = (isLocked: boolean) => {
        if (this.subject.value.isLocked !== isLocked) {
            this.subject.next({isLocked});
        }
    };

    isLocked = () => {
        return this.subject.value.isLocked;
    };

    observe() {
        return this.subject.asObservable();
    }

    getState() {
        return this.subject.value;
    }
}

const CalculatorLockStore = new CalculatorLockStoreSingleton();
export default CalculatorLockStore;
