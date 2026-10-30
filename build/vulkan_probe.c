/* SPDX-License-Identifier: MIT
 * Enumerate adapters through a specified Vulkan loader, without linking it.
 * --check-hybrid requires lavapipe CPU + GPU + Metal surface + swapchain.
 * This checks advertised capabilities, not actual presentation or Paint.NET.
 */
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

static int has_extension(VkExtensionProperties *ext, uint32_t count, const char *name) {
  for (uint32_t i = 0; i < count; i++)
    if (!strcmp(ext[i].extensionName, name)) return 1;
  return 0;
}

int main(int argc, char **argv) {
  int check = argc == 3 && !strcmp(argv[2], "--check-hybrid");
  if (argc < 2 || argc > 3 || (argc == 3 && !check)) {
    fprintf(stderr, "usage: %s /path/to/libvulkan.dylib [--check-hybrid]\n", argv[0]);
    return 2;
  }
  void *lib = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  if (!lib) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 1; }
  PFN_vkGetInstanceProcAddr get = (PFN_vkGetInstanceProcAddr)dlsym(lib, "vkGetInstanceProcAddr");
  if (!get) { fprintf(stderr, "Loader has no vkGetInstanceProcAddr\n"); dlclose(lib); return 1; }
  PFN_vkEnumerateInstanceExtensionProperties instance_extensions =
    (PFN_vkEnumerateInstanceExtensionProperties)get(NULL, "vkEnumerateInstanceExtensionProperties");
  PFN_vkCreateInstance create = (PFN_vkCreateInstance)get(NULL, "vkCreateInstance");
  if (!instance_extensions || !create) { dlclose(lib); return 1; }
  uint32_t ext_count = 0;
  VkResult result = instance_extensions(NULL, &ext_count, NULL);
  if (result != VK_SUCCESS || ext_count > 4096) { dlclose(lib); return 1; }
  VkExtensionProperties *ext = calloc(ext_count ? ext_count : 1, sizeof(*ext));
  if (!ext) { dlclose(lib); return 1; }
  result = instance_extensions(NULL, &ext_count, ext);
  if (result != VK_SUCCESS) { free(ext); dlclose(lib); return 1; }
  int metal = has_extension(ext, ext_count, "VK_EXT_metal_surface");
  int portability = has_extension(ext, ext_count, "VK_KHR_portability_enumeration");
  printf("instance: VK_EXT_metal_surface=%s VK_KHR_portability_enumeration=%s\n",
         metal ? "yes" : "no", portability ? "yes" : "no");
  free(ext);
  const char *enabled[] = {"VK_KHR_portability_enumeration"};
  VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
      .pApplicationName = "paintnet-vulkan-probe", .apiVersion = VK_API_VERSION_1_1};
  VkInstanceCreateInfo info = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
      .pApplicationInfo = &app,
      .flags = portability ? VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR : 0,
      .enabledExtensionCount = portability ? 1 : 0, .ppEnabledExtensionNames = enabled};
  VkInstance instance = VK_NULL_HANDLE;
  result = create(&info, NULL, &instance);
  if (result != VK_SUCCESS) {
    fprintf(stderr, "vkCreateInstance: %d\n", result); dlclose(lib); return 1;
  }
  PFN_vkEnumeratePhysicalDevices enumerate =
    (PFN_vkEnumeratePhysicalDevices)get(instance, "vkEnumeratePhysicalDevices");
  PFN_vkGetPhysicalDeviceProperties properties =
    (PFN_vkGetPhysicalDeviceProperties)get(instance, "vkGetPhysicalDeviceProperties");
  PFN_vkEnumerateDeviceExtensionProperties extensions =
    (PFN_vkEnumerateDeviceExtensionProperties)get(instance, "vkEnumerateDeviceExtensionProperties");
  PFN_vkDestroyInstance destroy = (PFN_vkDestroyInstance)get(instance, "vkDestroyInstance");
  if (!enumerate || !properties || !extensions || !destroy) { dlclose(lib); return 1; }
  uint32_t count = 0;
  result = enumerate(instance, &count, NULL);
  if (result != VK_SUCCESS || count > 256 || count == 0) {
    fprintf(stderr, "vkEnumeratePhysicalDevices: result=%d count=%u\n", result, count);
    destroy(instance, NULL); dlclose(lib); return 1;
  }
  VkPhysicalDevice *devices = calloc(count, sizeof(*devices));
  if (!devices) { destroy(instance, NULL); dlclose(lib); return 1; }
  result = enumerate(instance, &count, devices);
  int cpu = 0, gpu = 0;
  printf("devices=%u result=%d\n", count, result);
  for (uint32_t i = 0; result == VK_SUCCESS && i < count; i++) {
    VkPhysicalDeviceProperties prop;
    properties(devices[i], &prop);
    uint32_t n = 0;
    VkResult er = extensions(devices[i], NULL, &n, NULL);
    if (er != VK_SUCCESS || n > 4096) { result = VK_ERROR_INITIALIZATION_FAILED; break; }
    VkExtensionProperties *device_ext = calloc(n ? n : 1, sizeof(*device_ext));
    if (!device_ext) { result = VK_ERROR_OUT_OF_HOST_MEMORY; break; }
    er = extensions(devices[i], NULL, &n, device_ext);
    int swapchain = er == VK_SUCCESS && has_extension(device_ext, n, "VK_KHR_swapchain");
    free(device_ext);
    printf("%u: %s type=%u api=%u.%u.%u VK_KHR_swapchain=%s\n", i, prop.deviceName,
      prop.deviceType, VK_API_VERSION_MAJOR(prop.apiVersion), VK_API_VERSION_MINOR(prop.apiVersion),
      VK_API_VERSION_PATCH(prop.apiVersion), swapchain ? "yes" : "no");
    if (prop.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU && strstr(prop.deviceName, "llvmpipe") && swapchain) cpu = 1;
    if ((prop.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU ||
         prop.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU) && swapchain) gpu = 1;
    if (er != VK_SUCCESS) result = er;
  }
  free(devices);
  destroy(instance, NULL);
  dlclose(lib);
  if (result != VK_SUCCESS) return 1;
  if (check && !(cpu && gpu && metal)) {
    fprintf(stderr, "Hybrid check failed: lavapipe CPU+swapchain=%d GPU+swapchain=%d metal_surface=%d\n", cpu, gpu, metal);
    return 1;
  }
  if (check) puts("Hybrid capability check passed (presentation not exercised)");
  return 0;
}
