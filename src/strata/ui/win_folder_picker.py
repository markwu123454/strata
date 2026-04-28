"""
Native Windows folder picker using the Vista+ IFileOpenDialog COM
interface with FOS_PICKFOLDERS.

This is the modern Windows folder picker — the same one Explorer, Office,
etc. use. It has the breadcrumb path bar, favorites/quick access pane,
network locations, and proper keyboard navigation.

We call it via ctypes so we don't take a dependency on pywin32 or comtypes.

Usage:
    from strata.ui.win_folder_picker import pick_folder
    path = pick_folder(title="Choose folder", initial_dir=r"C:\\Users")
    if path:
        ...  # user picked, path is a str
    else:
        ...  # user cancelled
"""
from __future__ import annotations

import sys
from ctypes import (
    HRESULT, POINTER, byref, c_int, c_uint32, c_ulong, c_void_p, c_wchar_p,
    oledll, windll, WinError,
)
from ctypes.wintypes import DWORD, HWND, LPCWSTR, LPWSTR


# ── COM constants ─────────────────────────────────────────────────────────

CLSCTX_INPROC_SERVER = 0x1

COINIT_APARTMENTTHREADED = 0x2
COINIT_DISABLE_OLE1DDE   = 0x4

# IFileOpenDialog options (FILEOPENDIALOGOPTIONS enum)
FOS_PICKFOLDERS    = 0x00000020
FOS_FORCEFILESYSTEM = 0x00000040  # only return real filesystem paths
FOS_NOCHANGEDIR    = 0x00000008

# SIGDN values for IShellItem::GetDisplayName
SIGDN_FILESYSPATH  = 0x80058000

# E_* HRESULTs we care about
E_CANCELLED_HEX = 0x800704C7  # user cancelled
S_OK = 0


# CLSIDs / IIDs as GUID structures.
# We declare GUID inline rather than importing from a third-party lib.
from ctypes import Structure
from ctypes.wintypes import BYTE, WORD


class GUID(Structure):
    _fields_ = [
        ("Data1", DWORD),
        ("Data2", WORD),
        ("Data3", WORD),
        ("Data4", BYTE * 8),
    ]

    def __init__(self, guid_str: str):
        super().__init__()
        # Parse "{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}"
        windll.ole32.CLSIDFromString(c_wchar_p(guid_str), byref(self))


CLSID_FileOpenDialog = "{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}"
IID_IFileOpenDialog  = "{D57C7288-D4AD-4768-BE02-9D969532D960}"
IID_IShellItem       = "{43826D1E-E718-42EE-BC55-A1E261C37BFE}"


# ── ole32 / shell32 prototypes ────────────────────────────────────────────

ole32 = windll.ole32

ole32.CoInitializeEx.argtypes = [c_void_p, DWORD]
ole32.CoInitializeEx.restype = HRESULT

ole32.CoUninitialize.argtypes = []
ole32.CoUninitialize.restype = None

ole32.CoCreateInstance.argtypes = [
    POINTER(GUID), c_void_p, DWORD, POINTER(GUID), POINTER(c_void_p),
]
ole32.CoCreateInstance.restype = HRESULT

ole32.CoTaskMemFree.argtypes = [c_void_p]
ole32.CoTaskMemFree.restype = None


# ── Calling COM methods via vtable ────────────────────────────────────────
#
# A COM interface pointer is a pointer to a struct whose first field is a
# pointer to a vtable (an array of function pointers). Each method is at a
# known index in the vtable. To call one, we:
#   1. Read the vtable pointer from *p_interface
#   2. Read the method pointer at the right index
#   3. Cast it to a C function with the right signature
#   4. Call it, passing the interface pointer as the first argument (this)
#
# IUnknown vtable layout (always first 3 slots):
#   0: QueryInterface
#   1: AddRef
#   2: Release
#
# IFileDialog (which IFileOpenDialog inherits from) adds:
#   3:  Show(hwndOwner)
#   4:  SetFileTypes
#   5:  SetFileTypeIndex
#   6:  GetFileTypeIndex
#   7:  Advise
#   8:  Unadvise
#   9:  SetOptions(DWORD)
#   10: GetOptions
#   11: SetDefaultFolder
#   12: SetFolder
#   13: GetFolder
#   14: GetCurrentSelection
#   15: SetFileName
#   16: GetFileName
#   17: SetTitle(LPCWSTR)
#   18: SetOkButtonLabel
#   19: SetFileNameLabel
#   20: GetResult(IShellItem**)
#   ...
#
# IShellItem vtable (after IUnknown):
#   3: BindToHandler
#   4: GetParent
#   5: GetDisplayName(SIGDN, LPWSTR*)
#   ...

def _vtable_call(interface_ptr, index, restype, argtypes, *args):
    """Call method `index` on COM interface at `interface_ptr`."""
    from ctypes import CFUNCTYPE, cast, POINTER as P
    # Read vtable pointer
    vtbl_ptr = c_void_p.from_address(interface_ptr).value
    # Read function pointer at the given slot (slots are 8 bytes on x64)
    ptr_size = 8 if sys.maxsize > 2**32 else 4
    fn_addr = c_void_p.from_address(vtbl_ptr + index * ptr_size).value
    # Build the function signature: first arg is always the interface ptr (this)
    fn_type = CFUNCTYPE(restype, c_void_p, *argtypes)
    fn = fn_type(fn_addr)
    return fn(interface_ptr, *args)


def _release(interface_ptr):
    """IUnknown::Release — decrement refcount."""
    if interface_ptr:
        _vtable_call(interface_ptr, 2, c_ulong, [])


# ── Public API ────────────────────────────────────────────────────────────

def pick_folder(
    title: str = "Select folder",
    initial_dir: str | None = None,
    parent_hwnd: int = 0,
) -> str | None:
    """
    Show the Windows folder picker. Returns the selected path as a str, or
    None if the user cancelled or an error occurred.

    Must be called from a thread where COM can be initialized as STA, which
    in practice means the main UI thread.
    """
    if not sys.platform.startswith("win"):
        return None

    # Initialize COM for this thread. CoInitializeEx returns S_FALSE if
    # already initialized in a compatible mode; that's fine.
    hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE)
    # We don't unconditionally CoUninitialize — if Toga/another lib already
    # initialized COM, calling Uninitialize would balance our init but we
    # still want to leave their state alone. We only call it if WE were the
    # ones who initialized (S_OK), not if it was already up (S_FALSE = 1).
    we_initialized_com = (hr == 0)

    dialog_ptr = c_void_p()
    result_item = c_void_p()
    folder_path: str | None = None

    try:
        clsid = GUID(CLSID_FileOpenDialog)
        iid = GUID(IID_IFileOpenDialog)

        hr = ole32.CoCreateInstance(
            byref(clsid), None, CLSCTX_INPROC_SERVER, byref(iid), byref(dialog_ptr)
        )
        if hr != S_OK or not dialog_ptr.value:
            return None

        # GetOptions (slot 10) → current options
        current_opts = DWORD(0)
        _vtable_call(
            dialog_ptr.value, 10, HRESULT, [POINTER(DWORD)], byref(current_opts)
        )

        # SetOptions (slot 9) with FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM
        new_opts = current_opts.value | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_NOCHANGEDIR
        _vtable_call(dialog_ptr.value, 9, HRESULT, [DWORD], DWORD(new_opts))

        # SetTitle (slot 17)
        if title:
            _vtable_call(
                dialog_ptr.value, 17, HRESULT, [LPCWSTR], c_wchar_p(title)
            )

        # SetFolder (slot 12) — initial directory. We need an IShellItem
        # for the path; SHCreateItemFromParsingName builds one for us.
        if initial_dir:
            shell_item = c_void_p()
            shell_iid = GUID(IID_IShellItem)
            hr_item = windll.shell32.SHCreateItemFromParsingName(
                c_wchar_p(initial_dir), None, byref(shell_iid), byref(shell_item)
            )
            if hr_item == S_OK and shell_item.value:
                _vtable_call(
                    dialog_ptr.value, 12, HRESULT, [c_void_p], shell_item
                )
                _release(shell_item.value)

        # Show (slot 3) — blocks until user picks or cancels.
        # Returns 0x800704C7 on cancel.
        hr_show = _vtable_call(
            dialog_ptr.value, 3, HRESULT, [HWND], HWND(parent_hwnd)
        )
        # ctypes returns negative ints for HRESULT 0x80000000+; normalize:
        hr_show_u = hr_show & 0xFFFFFFFF
        if hr_show_u == E_CANCELLED_HEX:
            return None
        if hr_show != S_OK:
            return None

        # GetResult (slot 20) → IShellItem
        hr = _vtable_call(
            dialog_ptr.value, 20, HRESULT, [POINTER(c_void_p)], byref(result_item)
        )
        if hr != S_OK or not result_item.value:
            return None

        # IShellItem::GetDisplayName (slot 5) with SIGDN_FILESYSPATH → str
        path_ptr = LPWSTR()
        hr = _vtable_call(
            result_item.value, 5, HRESULT,
            [c_int, POINTER(LPWSTR)],
            SIGDN_FILESYSPATH, byref(path_ptr),
        )
        if hr == S_OK and path_ptr.value:
            folder_path = path_ptr.value
            ole32.CoTaskMemFree(path_ptr)

    finally:
        if result_item.value:
            _release(result_item.value)
        if dialog_ptr.value:
            _release(dialog_ptr.value)
        if we_initialized_com:
            ole32.CoUninitialize()

    return folder_path
