#ifndef GENOMICS_WASM_KALLISTO_SERIAL_THREAD_H
#define GENOMICS_WASM_KALLISTO_SERIAL_THREAD_H

#include <functional>
#include <pthread.h>
#include <utility>

namespace genomics_wasm {

// kallisto and Bifrost create workers even when their thread count is one.
// Run each submitted function to completion on the calling worker. This is
// only used by the explicitly single-thread build; it is not a general thread
// emulator and must not be used for producer/consumer or barrier-based code.
class SerialThread {
 public:
  SerialThread() = default;
  SerialThread(const SerialThread&) = delete;
  SerialThread& operator=(const SerialThread&) = delete;
  SerialThread(SerialThread&&) = default;
  SerialThread& operator=(SerialThread&&) = default;

  template <class Function, class... Arguments>
  explicit SerialThread(Function&& function, Arguments&&... arguments) {
    std::invoke(std::forward<Function>(function),
                std::forward<Arguments>(arguments)...);
  }

  void join() noexcept {}
  static unsigned hardware_concurrency() noexcept { return 1; }
};

// BooPHF's work queue is consumed by the same unchanged worker callback.
// Its return value is always null and the only joins discard that value.
inline int serial_pthread_create(pthread_t* thread, const pthread_attr_t*,
                                 void* (*function)(void*), void* argument) {
  *thread = pthread_t{};
  function(argument);
  return 0;
}
inline int serial_pthread_join(pthread_t, void** result) {
  if (result) *result = nullptr;
  return 0;
}

}  // namespace genomics_wasm
#endif
