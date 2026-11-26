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
#include <setjmp.h>

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


/* ---- calling into Proteus: generic thiscall with a crash guard (a vectored handler jumps back instead of killing Proteus) ---- */
static jmp_buf g_jb; static volatile int g_guard; static volatile DWORD g_exc;
static LONG CALLBACK veh(PEXCEPTION_POINTERS e) {
    if (g_guard && e->ExceptionRecord->ExceptionCode != DBG_PRINTEXCEPTION_C && (e->ExceptionRecord->ExceptionCode & 0xF0000000) == 0xC0000000) {
        g_exc = e->ExceptionRecord->ExceptionCode; g_guard = 0; longjmp(g_jb, 1);
    }
    return EXCEPTION_CONTINUE_SEARCH;
}
static unsigned long call_raw(unsigned long fn, unsigned long ecx, int thiscall, const unsigned long *a, int n) {
    unsigned long r, c = ecx, d = (unsigned long)n; (void)thiscall;
    __asm__ __volatile__(
        "movl %%esp, %%ebx\n\t"
        "1:\n\t" "testl %%edx, %%edx\n\t" "jz 2f\n\t" "decl %%edx\n\t" "pushl (%%esi,%%edx,4)\n\t" "jmp 1b\n"
        "2:\n\t" "call *%%edi\n\t"
        "movl %%ebx, %%esp"
        : "=a"(r), "+c"(c), "+d"(d) : "D"(fn), "S"(a) : "ebx", "memory", "cc");
    return r;
}
static int guarded_call(unsigned long fn, unsigned long ecx, const unsigned long *a, int n, unsigned long *ret) {
    if (setjmp(g_jb) == 0) { g_guard = 1; *ret = call_raw(fn, ecx, 1, a, n); g_guard = 0; return 1; }
    return 0;
}
static unsigned long hx(const char *s) { return strtoul(s, NULL, 16); }

static void run(const char *cmd, char *args, char *out) {
    if (!strcmp(cmd, "ping")) put(out, "pong pid=%lu", GetCurrentProcessId());
    else if (!strcmp(cmd, "modules")) {
        HANDLE s = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, GetCurrentProcessId());
        MODULEENTRY32 m; m.dwSize = sizeof m;
        if (Module32First(s, &m)) do put(out, "%p %lx %s\n", m.modBaseAddr, (unsigned long)m.modBaseSize, m.szModule); while (Module32Next(s, &m));
        CloseHandle(s);
    } else if (!strcmp(cmd, "windows")) EnumWindows(enum_win, (LPARAM)out);
    else if (!strcmp(cmd, "scratch")) { static unsigned char buf[4096]; put(out, "%p", buf); }
    else if (!strcmp(cmd, "mod")) { HMODULE m = GetModuleHandleA(args); put(out, "%p", m); }
    else if (!strcmp(cmd, "gmi")) {                       /* gmi <name|-> <flags hex>: getmoduleinterface from APPFRAME.DLL */
        typedef void *(__cdecl *Gmi)(const char *, unsigned);
        Gmi f = (Gmi)(void *)GetProcAddress(GetModuleHandleA("APPFRAME.DLL"), "?getmoduleinterface@@YAPAVIMODULE@@PBDI@Z");
        char *n = strtok(args, " "), *fl = strtok(NULL, " ");
        if (!f) { put(out, "ERR no getmoduleinterface"); return; }
        void *r = f(strcmp(n, "-") ? n : NULL, fl ? (unsigned)hx(fl) : 0x18);
        put(out, "%p", r);
    }
    else if (!strcmp(cmd, "call")) {                      /* call <fnaddr> <this|-> [args...] (hex; thiscall, ecx=this) */
        char *fn = strtok(args, " "), *th = strtok(NULL, " "); unsigned long a[16]; int n = 0; char *t;
        while ((t = strtok(NULL, " ")) && n < 16) a[n++] = hx(t);
        unsigned long r = 0;
        if (!fn || !th) { put(out, "ERR usage"); return; }
        if (guarded_call(hx(fn), strcmp(th, "-") ? hx(th) : 0, a, n, &r)) put(out, "%lx", r);
        else put(out, "ERR exception %lx", (unsigned long)g_exc);
    }
    else if (!strcmp(cmd, "scanvt")) {                    /* scanvt <lo> <hi> [maxaddrs]: heap objects whose first dword is in [lo,hi) */
        unsigned long lo = hx(strtok(args, " ")), hi = hx(strtok(NULL, " ")); char *m = strtok(NULL, " ");
        int mx = m ? atoi(m) : 2000, n = 0;
        MEMORY_BASIC_INFORMATION mb; unsigned char *p = 0;
        while (VirtualQuery(p, &mb, sizeof mb) == sizeof mb) {
            if (mb.State == MEM_COMMIT && (mb.Protect & (PAGE_READWRITE | PAGE_WRITECOPY)) && !(mb.Protect & PAGE_GUARD)) {
                unsigned long *q = (unsigned long *)mb.BaseAddress, *e = (unsigned long *)((char *)mb.BaseAddress + mb.RegionSize);
                for (; q < e; q++) if (*q >= lo && *q < hi && n < mx && strlen(out) < OUT_MAX - 40) { put(out, "%p %lx\n", q, *q); n++; }
            }
            p = (unsigned char *)mb.BaseAddress + mb.RegionSize; if (!p) break;
        }
    }
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
    AddVectoredExceptionHandler(1, veh);
    serve();
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD why, LPVOID r) {
    (void)r;
    if (why == DLL_PROCESS_ATTACH) { DisableThreadLibraryCalls(h); CreateThread(NULL, 0, thread_main, NULL, 0, NULL); }
    return TRUE;
}
