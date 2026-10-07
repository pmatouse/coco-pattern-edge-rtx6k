#include <cuda_runtime.h>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>

static void check(cudaError_t result) {
    if (result != cudaSuccess) {
        std::fprintf(stderr, "CUDA error: %s\n", cudaGetErrorString(result));
        std::exit(2);
    }
}

__global__ void add(const uint32_t *a, const uint32_t *b, uint32_t *c, uint32_t n) {
    uint32_t i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) c[i] = a[i] + b[i];
}

int main() {
    uint32_t n;
    if (std::fread(&n, sizeof(n), 1, stdin) != 1 || n == 0 || n > 16384) return 1;
    std::vector<uint32_t> a(n), b(n), c(n);
    if (std::fread(a.data(), sizeof(uint32_t), n, stdin) != n ||
        std::fread(b.data(), sizeof(uint32_t), n, stdin) != n ||
        std::fgetc(stdin) != EOF) return 1;
    uint32_t *da, *db, *dc;
    size_t bytes = n * sizeof(uint32_t);
    check(cudaSetDevice(0));
    check(cudaMalloc(&da, bytes));
    check(cudaMalloc(&db, bytes));
    check(cudaMalloc(&dc, bytes));
    check(cudaMemcpy(da, a.data(), bytes, cudaMemcpyHostToDevice));
    check(cudaMemcpy(db, b.data(), bytes, cudaMemcpyHostToDevice));
    add<<<(n + 255) / 256, 256>>>(da, db, dc, n);
    check(cudaGetLastError());
    check(cudaDeviceSynchronize());
    check(cudaMemcpy(c.data(), dc, bytes, cudaMemcpyDeviceToHost));
    check(cudaFree(da)); check(cudaFree(db)); check(cudaFree(dc));
    if (std::fwrite(c.data(), sizeof(uint32_t), n, stdout) != n) return 1;
    return std::fflush(stdout) == 0 ? 0 : 1;
}
