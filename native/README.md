# native/ - in-process hook (experimental)

`p2hook.dll` is injected into the running `PDS.EXE` (32-bit) by `p2launch.exe --attach PDS.EXE p2hook.dll`.
A worker thread serves file requests in `C:\exchange`: write `hook.req` (`<id> <command> <args>`), read `hook.resp`.

Commands so far: `ping`, `modules`, `windows`, `peek <hexaddr> <len>`.

Goal: reach Proteus' own drawing interface (`IISISAUTODRAW`, used by `IMPORTER.dll`) so that schematics with any library
device are created by Proteus itself. Build: `make` (needs `i686-w64-mingw32-gcc`).

Findings: `ISIS.DLL` exports `createmoduleinstance(host)` (object size 0x24a8); class `ISISAUTODRAW` vtable at RVA 0x9abd8
(Ghidra image base 0x10000000; Wine loads ISIS.DLL at a different base, so use `modules` to rebase).
