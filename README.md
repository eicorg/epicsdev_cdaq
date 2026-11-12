# cDAQ EPICS PVAccess Server. Under Development.

Requires the NI-DAQmx system driver. The Python `nidaqmx` dependency does not
install the system driver. At startup, the server registers the network chassis
at `192.168.0.16` if it is not already registered, requesting reservation for
this host without overriding another host's reservation.

From this directory, install dependencies and start the server:

```sh
uv sync
uv run epicsdev-cdaq --chassis cDAQ1
```

Alternatively, run `python -m epicsdev_cdaq` with the dependencies installed.
Omitting `--chassis` serves supported modules in all registered cDAQ chassis.
Use `--chassis-ip 192.168.0.20` for a different network address, or
`--chassis-ip ''` to disable automatic registration. An already registered
`--chassis` name takes precedence over the IP address. If the IP is already
registered under a different name, the server reuses that name instead of
registering the device twice. New registrations use `--chassis` as the name
when supplied; otherwise NI-DAQmx chooses the name. The device list is refreshed
after registration before discovering modules. Registration is retained on exit.
Discovery happens at startup; restart after changing installed modules.

## PVs

The default prefix is `cdaq:0:`; use the positional device prefix and `--instance`
to change it. For each discovered module, the server publishes an array PV:

| Module | PV suffix | Values |
| --- | --- | --- |
| NI-9213 | `<module>:TC` | 16 thermocouple temperatures in degrees C |
| NI-9201 | `<module>:AI` | 8 voltage inputs |
| NI-9209 | `<module>:AI` | Voltage inputs reported by DAQmx |
| NI-9263 | `<module>:AO` | 4 writable voltage outputs |
| NI-9264 | `<module>:AO` | 16 writable voltage outputs |
| NI-9401 | `<module>:DI` | 8 digital inputs, 0 or 1 |

For example, `cdaq:0:cDAQ1Mod1:TC`. The `modules` PV lists supported module
names. Each module also has `:model`, `:channels` (physical channel names in
array order), and `:status` PVs. Input errors update status without stopping
other modules. Input values remain at their last reading on error; check status
before relying on them. Reads are on demand, not synchronized waveform acquisition.

Voltage channels use a -10 to +10 V range. NI-9213 uses built-in cold-junction
compensation and type K thermocouples by default; change the type with
`--thermocouple-type J` (B, E, J, K, N, R, S, and T are supported).

## Outputs

NI-9401 defaults to input. To configure a whole module as output:

```sh
uv run epicsdev-cdaq --chassis cDAQ1 --digital-output cDAQ1Mod4
```

This replaces that module's `:DI` PV with a writable `:DO` PV. Repeat the
option for additional modules. Mixed input/output directions within one module
are not supported.

Write a complete channel array to `:AO` or `:DO`; incorrect lengths and values
are rejected. Output PVs report the last successful command, not hardware
readback. Initial zero values are placeholders until the first write, indicated
by `:status = Not written`. The server does not write outputs during startup or
restore output commands from autosave. Stopping polling does not reset outputs;
shutdown behavior follows the device and driver configuration.

Use `--timeout` to change the DAQmx I/O timeout (default 10 seconds).
