// Adapted from Emscripten 3.1.64 src/library_pthread.js.
// Copyright 2015 The Emscripten Authors. All rights reserved.
// SPDX-License-Identifier: MIT
// See LICENSE.emscripten. The browser-runtime guard is this port's
// change. Keep the other branches aligned with the pinned SDK implementation.
addToLibrary({
  _emscripten_thread_mailbox_await__deps: ['$checkMailbox', '$PThread'],
  _emscripten_thread_mailbox_await: (pthread_ptr) => {
    // WebKit can leave a notified waitAsync promise pending until an unrelated
    // event wakes the runtime worker. Leaving waiting_async at its initial zero
    // selects Emscripten's existing postMessage notification path. Restrict this
    // to the browser main runtime; Node and child pthreads retain the SDK path.
    if (!ENVIRONMENT_IS_PTHREAD && !ENVIRONMENT_IS_NODE) return;
    if (typeof Atomics.waitAsync === 'function') {
      var wait = Atomics.waitAsync(HEAP32, {{{ getHeapOffset('pthread_ptr', 'i32') }}}, pthread_ptr);
#if ASSERTIONS
      assert(wait.async);
#endif
      wait.value.then(checkMailbox);
      var waitingAsync = pthread_ptr + {{{ C_STRUCTS.pthread.waiting_async }}};
      Atomics.store(HEAP32, {{{ getHeapOffset('waitingAsync', 'i32') }}}, 1);
    }
  },
});
