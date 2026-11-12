"""EPICS PVAccess server for National Instruments cDAQ."""
# pylint: disable=invalid-name,broad-exception-caught
__version__ = 'v0.0.1 2026-11-12'# 

import argparse
from dataclasses import dataclass
from functools import partial

import nidaqmx
from nidaqmx.constants import (
    LineGrouping, ProductCategory, TemperatureUnits, ThermocoupleType,
)
from nidaqmx.errors import DaqError
from nidaqmx.system import System
from nidaqmx.system.device import Device

from epicsdev import epicsdev as edev

DEVICE = 'cdaq:'

MODULE_TYPES = {
    'NI 9213': 'TC',
    'NI 9201': 'AI',
    'NI 9263': 'AO',
    'NI 9401': 'DI',
    'NI 9209': 'AI',
    'NI 9264': 'AO',
}


@dataclass
class Module:
    """One supported module and its on-demand DAQmx task."""
    name: str
    model: str
    kind: str
    channels: list[str]
    task: nidaqmx.Task


class C_:
    """Container for server state."""
    pargs = None
    modules = []


def close_tasks():
    """Release all DAQmx resources, including after partial initialization."""
    for module in C_.modules:
        try:
            module.task.close()
        except Exception as exc:
            edev.printi(f'{module.name}: cannot close task: {exc}')
    C_.modules = []


def discover_modules():
    """Discover supported modules, optionally restricted to one chassis."""
    system = System.local()
    devices = register_chassis(system, list(system.devices))
    if C_.pargs.chassis:
        chassis = next((device for device in devices
                        if device.name == C_.pargs.chassis), None)
        if chassis is None or chassis.product_category != ProductCategory.COMPACT_DAQ_CHASSIS:
            raise ValueError(f'Not a cDAQ chassis: {C_.pargs.chassis}')
    selected_outputs = set(C_.pargs.digital_output)
    found_outputs = set()
    try:
        for device in devices:
            if device.product_category != ProductCategory.C_SERIES_MODULE:
                continue
            if not device.compact_daq_chassis_device.name:
                continue
            if (C_.pargs.chassis
                    and device.compact_daq_chassis_device.name != C_.pargs.chassis):
                continue
            model = device.product_type.replace('-', ' ')
            kind = MODULE_TYPES.get(model)
            if kind is None:
                edev.printi(f'Skipping unsupported module {device.name}: {model}')
                continue
            if kind == 'DI' and device.name in selected_outputs:
                kind = 'DO'
                found_outputs.add(device.name)
            collection = {
                'TC': 'ai_physical_chans', 'AI': 'ai_physical_chans',
                'AO': 'ao_physical_chans', 'DI': 'di_lines', 'DO': 'do_lines',
            }[kind]
            channels = [channel.name for channel in getattr(device, collection)]
            if not channels:
                raise ValueError(f'{device.name}: no {kind} channels available')
            task = nidaqmx.Task()
            module = Module(device.name, model, kind, channels, task)
            C_.modules.append(module)
            physical = ','.join(channels)
            if kind == 'TC':
                task.ai_channels.add_ai_thrmcpl_chan(
                    physical, units=TemperatureUnits.DEG_C,
                    thermocouple_type=getattr(
                        ThermocoupleType, C_.pargs.thermocouple_type))
            elif kind == 'AI':
                task.ai_channels.add_ai_voltage_chan(
                    physical, min_val=-10.0, max_val=10.0)
            elif kind == 'AO':
                task.ao_channels.add_ao_voltage_chan(
                    physical, min_val=-10.0, max_val=10.0)
            else:
                channel_group = task.di_channels if kind == 'DI' else task.do_channels
                add_channels = (channel_group.add_di_chan if kind == 'DI'
                                else channel_group.add_do_chan)
                add_channels(physical, line_grouping=LineGrouping.CHAN_PER_LINE)
            edev.printi(f'{device.name}: {model}, {kind}, {len(channels)} channels')
        if selected_outputs != found_outputs:
            missing = ', '.join(sorted(selected_outputs - found_outputs))
            raise ValueError(f'NI-9401 output modules not found: {missing}')
        if not C_.modules:
            raise ValueError('No supported cDAQ modules found')
    except Exception:
        close_tasks()
        raise


def register_chassis(system, devices):
    """Register the network chassis only when not already known to DAQmx."""
    if any(device.name == C_.pargs.chassis for device in devices):
        return devices
    if not C_.pargs.chassis_ip:
        return devices
    for device in devices:
        if device.product_category != ProductCategory.COMPACT_DAQ_CHASSIS:
            continue
        try:
            address = device.tcpip_ethernet_ip
        except DaqError:
            continue
        if address == C_.pargs.chassis_ip:
            if C_.pargs.chassis:
                edev.printi(f'Chassis at {address} already registered as {device.name}')
                C_.pargs.chassis = device.name
            return devices
    chassis = Device.add_network_device(
        C_.pargs.chassis_ip, device_name=C_.pargs.chassis or '',
        attempt_reservation=True, timeout=C_.pargs.timeout)
    edev.printi(f'Registered network chassis {chassis.name} at {C_.pargs.chassis_ip}')
    return list(system.devices)


def set_output(module, value, *_):
    """Write a complete module vector, publishing only successful writes."""
    try:
        values = list(value)
        if len(values) != len(module.channels):
            raise ValueError(f'Expected {len(module.channels)} channel values')
        if module.kind == 'DO':
            if any(item not in (0, 1) for item in values):
                raise ValueError('Digital outputs must be 0 or 1')
            values = [bool(item) for item in values]
        else:
            values = [float(item) for item in values]
            if any(not -10.0 <= item <= 10.0 for item in values):
                raise ValueError('Analog outputs must be within -10 to 10 V')
        module.task.write(values, auto_start=True, timeout=C_.pargs.timeout)
        edev.publish(f'{module.name}:{module.kind}',
                     [int(item) for item in values] if module.kind == 'DO' else values)
        edev.publish(f'{module.name}:status', 'OK')
    except Exception as exc:
        edev.publish(f'{module.name}:status', str(exc))
        raise

#````````````````````````````````````````````````````````````````````````````
# Definitions of PVs, their types, units, limits, and setter functions are
# defined in the myPVDefs() function below.
def myPVDefs():
    """Return list of PV definitions"""
    # abbreviations for PV definition dictionary keys
    F, T, U = 'features', 'type', 'units'
    SET = 'setter'

    pv_defs = [
        ['dateTime', 'Server local date/time', 'N/A'],
        ['modules', 'Supported installed module names',
         [module.name for module in C_.modules]],
    ]
    for module in C_.modules:
        digital = module.kind in ('DI', 'DO')
        writable = module.kind in ('AO', 'DO')
        options = {T: 'u8' if digital else 'f64'}
        if not digital:
            options[U] = 'C' if module.kind == 'TC' else 'V'
        if writable:
            options.update({F: 'W', SET: partial(set_output, module)})
        pv_defs.extend([
            [f'{module.name}:model', 'Installed module model', module.model],
            [f'{module.name}:channels', 'Physical channels in array order', module.channels],
            [f'{module.name}:status', 'Last I/O status',
             'Not written' if writable else 'Not read'],
            [f'{module.name}:{module.kind}', f'{module.model} {module.kind} channels',
             [0 if digital else 0.0] * len(module.channels), options],
        ])
    return pv_defs
#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,

def poll():
    """Poll for updates from the hardware or other sources."""
    for module in C_.modules:
        if module.kind in ('AO', 'DO'):
            continue
        try:
            values = module.task.read(timeout=C_.pargs.timeout)
            if not isinstance(values, list):
                values = [values]
            if module.kind == 'DI':
                values = [int(value) for value in values]
            edev.publish(f'{module.name}:{module.kind}', values)
            edev.publish(f'{module.name}:status', 'OK', ifChanged=True)
        except Exception as exc:
            edev.publish(f'{module.name}:status', str(exc), ifChanged=True)

def periodic_update():
    """Perform periodic updates."""
    return

def serverStateChanged(newState: str):
    """Callback for server state transitions."""
    if newState == 'Start':
        edev.printi('Start requested')
    elif newState == 'Stop':
        edev.printi('Stop requested')
    elif newState == 'Exit':
        edev.printi('Exit requested')

def main():
    """Discover hardware and serve its channels over EPICS PVAccess."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=f'{__version__}, epicsdev:{edev.__version__}',
    )
    # commonly used options
    parser.add_argument('-a', '--autosave', nargs='?', default='', help=
'Autosave control. If omitted, autosave is enabled with default directory.')
    parser.add_argument('-c', '--recall', action='store_false', help=
'If given: do not restore initial PV values from autosave cache.')
    parser.add_argument('-i', '--instance', default='0', help=
'Device instance.')
    parser.add_argument('-p', '--putlogPV', nargs='?', default='', help=
'PV name for logging put operations. Empty means default putlog:dump.')
    parser.add_argument('-v', '--verbose', action='count', default=0, help=
'Increase verbosity (-vv for more).')
    parser.add_argument('device', nargs='?', default=DEVICE, help=
'Device name, the prefix for all generated PVs <prefix><instance>:')

    parser.add_argument('--chassis', help='DAQmx chassis name; default: all cDAQ chassis.')
    parser.add_argument('--chassis-ip', default='192.168.0.16', help=
                        'Register this network chassis if missing; empty disables registration.')
    parser.add_argument('--digital-output', action='append', default=[], metavar='MODULE',
                        help='Configure an entire NI-9401 as output (repeatable).')
    parser.add_argument('--thermocouple-type', choices=list('BEJKNRST'), default='K',
                        help='Thermocouple type for NI-9213 channels.')
    parser.add_argument('--timeout', type=float, default=10.0,
                        help='DAQmx read/write timeout in seconds.')
    
    # Parse command-line arguments and store them in the module state container.
    C_.pargs = parser.parse_args()
    if C_.pargs.putlogPV == '':
        C_.pargs.putlogPV = 'putlog:dump'
    C_.pargs.prefix = f'{C_.pargs.device}{C_.pargs.instance}:'

    if C_.pargs.timeout <= 0:
        parser.error('--timeout must be positive')
    try:
        discover_modules()
        run_server()
    finally:
        close_tasks()


def run_server():
    """Initialize PVAccess after discovery and run until exit is requested."""
    C_.PvDefs = myPVDefs()

    # Initialize the EPICS PVAccess server with the defined PVs.
    PVs = edev.init_epicsdev(
        C_.pargs.prefix,
        C_.PvDefs,
        C_.pargs.verbose,
        serverStateChanged,
        None,# to write list of PVs to /tmp/pvlist/
        C_.pargs.autosave,
        C_.pargs.recall,
        C_.pargs.putlogPV,
    )
    print(f'Using PV prefix: {C_.pargs.prefix}')

    # Start the server and print initial status.
    edev.publish('VERSION', __version__)
    edev.set_server('Start')
    server = edev.Server(providers=[PVs])
    edev.printi((f'Server for {C_.pargs.prefix} started. Sleeping per cycle: '
                 f'{repr(edev.pvv("sleep"))} S.'))

    # Main server loop. Polls for updates and handles server state transitions.
    try:
        while True:
            state = edev.serverState()
            if state.startswith('Exit'):
                break
            if not state.startswith('Stop'):
                poll()
            if not edev.sleep():
                periodic_update()
    except KeyboardInterrupt:
        edev.printi('Keyboard interrupt received, exiting main loop...')
        edev.set_server('Exit')
    except OSError:
        pass
    finally:
        server.stop()

    edev.printi('Server is exited')


if __name__ == '__main__':
    main()
