/* p2launch.exe --attach <process.exe> <dll>   load <dll> into a running 32-bit process found by name
 * (32-bit injector: Proteus is a 32-bit process, so LoadLibrary must come from the 32-bit kernel32). */
#include <windows.h>
#include <tlhelp32.h>
#include <stdio.h>
#include <string.h>

static DWORD find_pid(const char *name) {
    HANDLE s = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    PROCESSENTRY32 e; e.dwSize = sizeof e; DWORD pid = 0;
    if (s != INVALID_HANDLE_VALUE && Process32First(s, &e))
        do { if (!_stricmp(e.szExeFile, name)) { pid = e.th32ProcessID; break; } } while (Process32Next(s, &e));
    CloseHandle(s);
    return pid;
}

int main(int argc, char **argv) {
    if (argc != 4 || strcmp(argv[1], "--attach")) { fprintf(stderr, "usage: p2launch --attach <process.exe> <dll>\n"); return 2; }
    DWORD pid = find_pid(argv[2]);
    if (!pid) { fprintf(stderr, "no process %s\n", argv[2]); return 1; }
    HANDLE p = OpenProcess(PROCESS_ALL_ACCESS, FALSE, pid);
    if (!p) { fprintf(stderr, "OpenProcess failed: %lu\n", GetLastError()); return 1; }
    char dll[MAX_PATH]; GetFullPathNameA(argv[3], sizeof dll, dll, NULL);
    SIZE_T n = strlen(dll) + 1;
    void *mem = VirtualAllocEx(p, NULL, n, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!mem || !WriteProcessMemory(p, mem, dll, n, NULL)) { fprintf(stderr, "write failed: %lu\n", GetLastError()); return 1; }
    HANDLE t = CreateRemoteThread(p, NULL, 0, (LPTHREAD_START_ROUTINE)GetProcAddress(GetModuleHandleA("kernel32.dll"), "LoadLibraryA"), mem, 0, NULL);
    if (!t) { fprintf(stderr, "CreateRemoteThread failed: %lu\n", GetLastError()); return 1; }
    WaitForSingleObject(t, 15000);
    DWORD ok = 0; GetExitCodeThread(t, &ok);
    printf("pid=%lu loaded=%s\n", (unsigned long)pid, ok ? "yes" : "NO");
    return ok ? 0 : 1;
}
