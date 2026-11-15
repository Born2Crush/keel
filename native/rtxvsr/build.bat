@echo off
rem Builds rtxvsr.dll (the ctypes wrapper around NVIDIA's RTX Video SDK) with Visual Studio 2019+ Build Tools.
rem Needs the RTX Video SDK unpacked somewhere; point NV_RTX_VIDEO_SDK at it (the folder with include, lib, bin).
rem No CUDA Toolkit needed: the wrapper uses PyTorch's CUDA runtime at run time.
setlocal
if "%NV_RTX_VIDEO_SDK%"=="" (
  echo Set NV_RTX_VIDEO_SDK to the folder you unpacked the RTX Video SDK into
  exit /b 1
)
if not exist "%NV_RTX_VIDEO_SDK%\include\nvsdk_ngx.h" (
  echo RTX Video SDK not found at %NV_RTX_VIDEO_SDK% - set NV_RTX_VIDEO_SDK
  exit /b 1
)
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath > "%TEMP%\rtxvsr_vsdir.txt"
set /p VSDIR=<"%TEMP%\rtxvsr_vsdir.txt"
del "%TEMP%\rtxvsr_vsdir.txt"
if "%VSDIR%"=="" (
  echo Visual Studio C++ build tools not found
  exit /b 1
)
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
cd /d "%~dp0"
if not exist build mkdir build
cl /nologo /LD /MD /O2 /EHsc /W3 /I"%NV_RTX_VIDEO_SDK%\include" rtxvsr.cpp /Fobuild\ /Febuild\rtxvsr.dll ^
   /link /LIBPATH:"%NV_RTX_VIDEO_SDK%\lib\Windows\x64" nvsdk_ngx_d.lib advapi32.lib user32.lib || exit /b 1
echo built %~dp0build\rtxvsr.dll
