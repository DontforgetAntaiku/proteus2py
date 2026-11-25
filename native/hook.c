/* p2hook.dll - loaded into Proteus (PDS.EXE). A worker thread answers requests that arrive as files:
 *   <dir>\hook.req   "<id> <command> <args>"   (written by the client, then renamed into place)
 *   <dir>\hook.resp  "<id> ok <text>" or "<id> err <text>"
 * <dir> is %P2HOOK_DIR% (default C:\exchange). */
#include <windows.h>
#include <tlhelp32.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdarg.h>

static char g_dir[MAX_PATH];
#define OUT_MAX 65536

static void put(char *out, const char *fmt, ...) {
    size_t n = strlen(out);
    va_list ap; va_start(ap, fmt); vsnprintf(out + n, OUT_MAX - n, fmt, ap); va_end(ap);
}

static BOOL CALLBACK enum_win(HWND h, LPARAM param) {
    DWORD pid; GetWindowThreadProcessId(h, &pid);
    if (pid != GetCurrentProcessId() || !IsWindowVisible(h)) return TRUE;
    char t[200], c[100]; GetWindowTextA(h, t, sizeof t); GetClassNameA(h, c, sizeof c);
    put((char *)param, "%p %s | %s\n", h, c, t);
    return TRUE;
}

static void run(const char *cmd, char *args, char *out) {
    if (!strcmp(cmd, "ping")) put(out, "pong pid=%lu", GetCurrentProcessId());
    else if (!strcmp(cmd, "modules")) {
        HANDLE s = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, GetCurrentProcessId());
        MODULEENTRY32 m; m.dwSize = sizeof m;
        if (Module32First(s, &m)) do put(out, "%p %lx %s\n", m.modBaseAddr, (unsigned long)m.modBaseSize, m.szModule); while (Module32Next(s, &m));
        CloseHandle(s);
    } else if (!strcmp(cmd, "windows")) EnumWindows(enum_win, (LPARAM)out);
    else if (!strcmp(cmd, "peek")) {                        /* peek <hexaddr> <len> */
        unsigned long a = strtoul(strtok(args, " "), NULL, 16); char *l = strtok(NULL, " ");
        int n = l ? atoi(l) : 16; if (n > 4096) n = 4096;
        if (IsBadReadPtr((void *)a, n)) { put(out, "ERR unreadable"); return; }
        for (int i = 0; i < n; i++) put(out, "%02x", ((unsigned char *)a)[i]);
    } else put(out, "ERR unknown command '%s'", cmd);
}

static void serve(void) {
    char req[MAX_PATH * 2], resp[MAX_PATH * 2], tmp[MAX_PATH * 2];
    snprintf(req, sizeof req, "%s\\hook.req", g_dir);
    snprintf(resp, sizeof resp, "%s\\hook.resp", g_dir);
    snprintf(tmp, sizeof tmp, "%s\\hook.resp.tmp", g_dir);
    static char out[OUT_MAX];
    for (;;) {
        FILE *f = fopen(req, "r");
        if (!f) { Sleep(30); continue; }
        char line[4096] = ""; fgets(line, sizeof line, f); fclose(f); DeleteFileA(req);
        char *p = line + strlen(line); while (p > line && (p[-1] == '\n' || p[-1] == '\r')) *--p = 0;
        char *id = strtok(line, " "), *cmd = strtok(NULL, " "), *args = strtok(NULL, "");
        if (!id || !cmd) continue;
        out[0] = 0; run(cmd, args ? args : "", out);
        FILE *o = fopen(tmp, "w");
        if (o) { fprintf(o, "%s %s %s", id, strncmp(out, "ERR ", 4) ? "ok" : "err", strncmp(out, "ERR ", 4) ? out : out + 4); fclose(o); MoveFileExA(tmp, resp, MOVEFILE_REPLACE_EXISTING); }
    }
}

static DWORD WINAPI thread_main(LPVOID u) {
    (void)u;
    const char *d = getenv("P2HOOK_DIR"); snprintf(g_dir, sizeof g_dir, "%s", d ? d : "C:\\exchange");
    serve();
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD why, LPVOID r) {
    (void)r;
    if (why == DLL_PROCESS_ATTACH) { DisableThreadLibraryCalls(h); CreateThread(NULL, 0, thread_main, NULL, 0, NULL); }
    return TRUE;
}
