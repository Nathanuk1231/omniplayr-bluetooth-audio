import { Bluetooth } from 'lucide-react';
import { player, registerTab } from '@omniplayr/plugins';
import BluetoothAudioSourcePlugin from './BluetoothAudioSourcePlugin';
import BluetoothAudioTab from './BluetoothAudioTab';

const PLUGIN_ID = 'bluetooth-audio@Nathanuk1231';
const SOURCE_TYPE = 'bluetooth-audio';

export function init() {
    player.registerPlugin(SOURCE_TYPE, new BluetoothAudioSourcePlugin());
    registerTab(PLUGIN_ID, {
        icon: Bluetooth,
        view: BluetoothAudioTab,
        label: 'Bluetooth Audio',
        sourceType: SOURCE_TYPE,
    });
}

init();
