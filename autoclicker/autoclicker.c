// Простой автокликер для Windows. F6 — старт/стоп (работает в любом окне).
// Сборка: x86_64-w64-mingw32-gcc -O2 -municode -mwindows autoclicker.c -o AutoClicker.exe -lwinmm -s
#include <windows.h>
#include <stdlib.h>

#define ID_INTERVAL 101
#define ID_LEFT     102
#define ID_RIGHT    103
#define ID_START    104
#define ID_STATUS   105
#define ID_HOTKEY   1

static HWND hWnd, hInterval, hLeft, hStart, hStatus;
static volatile LONG running = 0;
static volatile LONG intervalMs = 100;
static volatile LONG rightBtn = 0;
static volatile LONG clicks = 0;
static HANDLE hThread;
static volatile LONG startAt = 0; // GetTickCount, когда начинать кликать

static void click(void) {
    INPUT in[2] = {0};
    in[0].type = in[1].type = INPUT_MOUSE;
    in[0].mi.dwFlags = rightBtn ? MOUSEEVENTF_RIGHTDOWN : MOUSEEVENTF_LEFTDOWN;
    in[1].mi.dwFlags = rightBtn ? MOUSEEVENTF_RIGHTUP : MOUSEEVENTF_LEFTUP;
    SendInput(2, in, sizeof(INPUT));
}

static DWORD WINAPI worker(LPVOID p) {
    (void)p;
    while (running && (LONG)(GetTickCount() - startAt) < 0) Sleep(20);
    while (running) {
        click();
        InterlockedIncrement(&clicks);
        Sleep((DWORD)intervalMs);
    }
    return 0;
}

static void setStatus(void) {
    wchar_t buf[96];
    LONG left = startAt - (LONG)GetTickCount();
    if (running && left > 0) wsprintfW(buf, L"Старт через %ld с — уведите курсор", (left + 999) / 1000);
    else if (running) wsprintfW(buf, L"Работает · кликов: %ld", clicks);
    else wsprintfW(buf, L"Остановлен · кликов: %ld", clicks);
    SetWindowTextW(hStatus, buf);
}

static void toggle(int delayMs) {
    if (running) {
        InterlockedExchange(&running, 0);
        if (hThread) { WaitForSingleObject(hThread, 2000); CloseHandle(hThread); hThread = NULL; }
        KillTimer(hWnd, 1);
        SetWindowTextW(hStart, L"Старт (F6)");
        EnableWindow(hInterval, TRUE);
    } else {
        wchar_t buf[32];
        GetWindowTextW(hInterval, buf, 32);
        long v = wcstol(buf, NULL, 10);
        if (v < 1) v = 1;
        if (v > 3600000) v = 3600000;
        wsprintfW(buf, L"%ld", v);
        SetWindowTextW(hInterval, buf);
        intervalMs = v;
        rightBtn = (SendMessageW(hLeft, BM_GETCHECK, 0, 0) == BST_CHECKED) ? 0 : 1;
        clicks = 0;
        startAt = (LONG)GetTickCount() + delayMs;
        InterlockedExchange(&running, 1);
        hThread = CreateThread(NULL, 0, worker, NULL, 0, NULL);
        SetTimer(hWnd, 1, 100, NULL);
        SetWindowTextW(hStart, L"Стоп (F6)");
        EnableWindow(hInterval, FALSE);
    }
    setStatus();
}

static HWND mk(const wchar_t *cls, const wchar_t *txt, DWORD style, int x, int y, int w, int h, int id, HFONT f) {
    HWND c = CreateWindowExW(0, cls, txt, WS_CHILD | WS_VISIBLE | style, x, y, w, h, hWnd, (HMENU)(INT_PTR)id, GetModuleHandleW(NULL), NULL);
    SendMessageW(c, WM_SETFONT, (WPARAM)f, TRUE);
    return c;
}

static LRESULT CALLBACK WndProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_CREATE: {
        hWnd = h;
        HFONT f = CreateFontW(-15, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Segoe UI");
        mk(L"STATIC", L"Интервал, мс:", 0, 16, 18, 110, 22, 0, f);
        hInterval = mk(L"EDIT", L"100", WS_BORDER | ES_NUMBER, 130, 15, 144, 24, ID_INTERVAL, f);
        hLeft = mk(L"BUTTON", L"Левая", BS_AUTORADIOBUTTON | WS_GROUP, 16, 52, 90, 22, ID_LEFT, f);
        mk(L"BUTTON", L"Правая", BS_AUTORADIOBUTTON, 120, 52, 90, 22, ID_RIGHT, f);
        SendMessageW(hLeft, BM_SETCHECK, BST_CHECKED, 0);
        hStart = mk(L"BUTTON", L"Старт (F6)", BS_DEFPUSHBUTTON, 16, 86, 258, 34, ID_START, f);
        hStatus = mk(L"STATIC", L"", 0, 16, 130, 260, 22, ID_STATUS, f);
        setStatus();
        if (!RegisterHotKey(h, ID_HOTKEY, MOD_NOREPEAT, VK_F6))
            MessageBoxW(h, L"F6 занята другой программой — используйте кнопку.", L"Автокликер", MB_ICONWARNING);
        timeBeginPeriod(1);
        return 0;
    }
    case WM_COMMAND:
        if (LOWORD(w) == ID_START) toggle(3000); // кнопка: 3 с, чтобы увести курсор с окна
        else if ((LOWORD(w) == ID_LEFT || LOWORD(w) == ID_RIGHT) && running)
            rightBtn = LOWORD(w) == ID_RIGHT;
        return 0;
    case WM_HOTKEY:
        if (w == ID_HOTKEY) toggle(0);
        return 0;
    case WM_TIMER:
        setStatus();
        return 0;
    case WM_CTLCOLORSTATIC:
        SetBkMode((HDC)w, TRANSPARENT);
        return (LRESULT)GetSysColorBrush(COLOR_WINDOW);
    case WM_DESTROY:
        if (running) toggle(0);
        UnregisterHotKey(h, ID_HOTKEY);
        timeEndPeriod(1);
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

int WINAPI wWinMain(HINSTANCE hi, HINSTANCE hp, LPWSTR cmd, int show) {
    (void)hp; (void)cmd;
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hi;
    wc.hCursor = LoadCursorW(NULL, (LPCWSTR)IDC_ARROW);
    wc.hIcon = LoadIconW(NULL, (LPCWSTR)IDI_APPLICATION);
    wc.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    wc.lpszClassName = L"SimpleAutoClicker";
    RegisterClassW(&wc);
    RECT r = {0, 0, 290, 162};
    DWORD st = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    AdjustWindowRect(&r, st, FALSE);
    HWND h = CreateWindowExW(WS_EX_TOPMOST, wc.lpszClassName, L"Автокликер", st,
                             CW_USEDEFAULT, CW_USEDEFAULT, r.right - r.left, r.bottom - r.top, NULL, NULL, hi, NULL);
    ShowWindow(h, show);
    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0)) {
        if (!IsDialogMessageW(h, &msg)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
    }
    return 0;
}
