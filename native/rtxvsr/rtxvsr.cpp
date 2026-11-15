// Minimal C interface to NVIDIA RTX Video Super Resolution (RTX Video SDK, CUDA path), loaded from Python
// with ctypes (framegen/rtxvsr.py). Build with build.bat; needs the RTX Video SDK (NV_RTX_VIDEO_SDK).
//
// Return codes: 0 = success; -1 = VSR not available on this GPU/driver; -2 = needs a newer driver;
// -3 = not created yet; anything else is an NVSDK_NGX_Result (0xBAD000xx).
#include <windows.h>

#include <nvsdk_ngx.h>
#include <nvsdk_ngx_helpers_vsr.h>

#define API extern "C" __declspec(dllexport)

static NVSDK_NGX_Parameter* g_params = nullptr;
static NVSDK_NGX_Handle* g_vsr = nullptr;
static bool g_init = false;

static int code(NVSDK_NGX_Result r) { return NVSDK_NGX_FAILED(r) ? (int)r : 0; }

// NGX's static library calls three CUDA runtime functions. Rather than linking the CUDA Toolkit's cudart.lib,
// forward them to the CUDA runtime DLL PyTorch has already loaded (same runtime, same primary context).
static HMODULE g_cudart = nullptr;

template <typename F> static F cudart_fn(const char* name)
{
    return g_cudart ? reinterpret_cast<F>(GetProcAddress(g_cudart, name)) : nullptr;
}

extern "C" int cudaGetDevice(int* device)
{
    auto f = cudart_fn<int (*)(int*)>("cudaGetDevice");
    return f ? f(device) : 35;  // cudaErrorInsufficientDriver
}

extern "C" int cudaGetDeviceProperties(void* prop, int device)
{
    auto f = cudart_fn<int (*)(void*, int)>("cudaGetDeviceProperties");
    return f ? f(prop, device) : 35;
}

extern "C" const char* cudaGetErrorString(int error)
{
    auto f = cudart_fn<const char* (*)(int)>("cudaGetErrorString");
    return f ? f(error) : "CUDA runtime not loaded";
}

// Path of the CUDA runtime DLL to use (PyTorch's cudart64_12.dll). Call before rtxvsr_create. 0 = loaded.
API int rtxvsr_set_cudart(const wchar_t* path)
{
    g_cudart = LoadLibraryW(path);
    return g_cudart ? 0 : (int)GetLastError();
}

// cu_context / cu_stream: the CUcontext and CUstream VSR runs on (PyTorch's).
// feature_dir: folder holding nvngx_vsr.dll. data_dir: where NGX may write logs and caches.
API int rtxvsr_create(void* cu_context, void* cu_stream, const wchar_t* feature_dir, const wchar_t* data_dir)
{
    const wchar_t* paths[1] = {feature_dir};
    NVSDK_NGX_FeatureCommonInfo info = {};
    info.PathListInfo.Path = paths;
    info.PathListInfo.Length = feature_dir ? 1 : 0;
    NVSDK_NGX_Result r = NVSDK_NGX_CUDA_Init(0, data_dir, &info);
    if (NVSDK_NGX_FAILED(r))
        return (int)r;
    g_init = true;
    r = NVSDK_NGX_CUDA_GetCapabilityParameters(&g_params);
    if (NVSDK_NGX_FAILED(r))
        return (int)r;
    int available = 0, needs_driver = 0;
    NVSDK_NGX_Parameter_GetI(g_params, NVSDK_NGX_Parameter_VSR_Available, &available);
    if (!available) {
        NVSDK_NGX_Parameter_GetI(g_params, NVSDK_NGX_Parameter_VSR_NeedsUpdatedDriver, &needs_driver);
        return needs_driver ? -2 : -1;
    }
    NVSDK_NGX_CUDA_VSRCreate_Params cp = {};
    cp.InCUContext = cu_context;
    cp.InCUStream = cu_stream;
    return code(NGX_CUDA_CREATE_VSR(&g_vsr, g_params, &cp));
}

// tex_in: CUtexObject over an 8-bit RGBA CUDA array (normalised coordinates, linear filtering, clamp).
// surf_out: CUsurfObject over an 8-bit RGBA CUDA array; VSR writes the out_* rectangle of it.
// quality: 0 = bicubic, 1 = low ... 4 = ultra. Work is queued on the stream given to rtxvsr_create.
API int rtxvsr_evaluate(unsigned long long tex_in, unsigned long long surf_out, unsigned int in_w,
                        unsigned int in_h, unsigned int out_x, unsigned int out_y, unsigned int out_w,
                        unsigned int out_h, int quality)
{
    if (!g_vsr)
        return -3;
    NVSDK_NGX_CUDA_VSR_Eval_Params ep = {};
    ep.pInput = &tex_in;
    ep.pOutput = &surf_out;
    ep.InputSubrectBase = {0, 0};
    ep.InputSubrectSize = {in_w, in_h};
    ep.OutputSubrectBase = {out_x, out_y};
    ep.OutputSubrectSize = {out_w, out_h};
    ep.QualityLevel = (NVSDK_NGX_VSR_QualityLevel)quality;
    return code(NGX_CUDA_EVALUATE_VSR(g_vsr, g_params, &ep));
}

API void rtxvsr_shutdown(void)
{
    if (g_vsr)
        NVSDK_NGX_CUDA_ReleaseFeature(g_vsr);
    if (g_params)
        NVSDK_NGX_CUDA_DestroyParameters(g_params);
    if (g_init)
        NVSDK_NGX_CUDA_Shutdown();
    g_vsr = nullptr;
    g_params = nullptr;
    g_init = false;
}
