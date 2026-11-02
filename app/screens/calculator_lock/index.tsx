// Copyright (c) 2015-present Mattermost, Inc. All Rights Reserved.
// See LICENSE.txt for license information.

import {Portal} from '@gorhom/portal';
import {useState} from 'react';
import {Platform, StyleSheet, View} from 'react-native';
import {FullWindowOverlay} from 'react-native-screens';

import useDidMount from '@hooks/did_mount';
import CalculatorLockManager from '@managers/calculator_lock_manager';
import CalculatorLockStore from '@store/calculator_lock_store';

import CalculatorLock from './calculator_lock';

const styles = StyleSheet.create({
    container: {
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        zIndex: 2000,
        elevation: 2000,
    },
});

/**
 * Renders the disguised calculator above every other surface (including
 * native modals) while Calculator Lock is engaged.
 */
export default function CalculatorLockContainer() {
    const [state, setState] = useState(CalculatorLockStore.getState());

    useDidMount(() => {
        const sub = CalculatorLockStore.observe().subscribe(setState);
        return () => sub.unsubscribe();
    });

    if (!state.isLocked) {
        return null;
    }

    const Container = Platform.OS === 'ios' ? FullWindowOverlay : View;

    return (
        <Portal hostName='calculator_lock'>
            <Container style={styles.container}>
                <CalculatorLock onUnlock={CalculatorLockManager.unlock}/>
            </Container>
        </Portal>
    );
}
