/* SPDX-License-Identifier: MIT
 * Sequential implementation of the callback interfaces in BWA's kthread.c.
 * Each item and pipeline stage is executed; no alignment code is replaced.
 */
#include <stddef.h>

void kt_for(int n_threads, void (*func)(void *, long, int), void *data, long n)
{
    long i;
    (void)n_threads;
    for (i = 0; i < n; ++i) func(data, i, 0);
}

void kt_pipeline(int n_threads, void *(*func)(void *, int, void *),
                 void *shared_data, int n_steps)
{
    (void)n_threads;
    if (n_steps < 1) return;
    for (;;) {
        int step;
        void *data = func(shared_data, 0, NULL);
        if (data == NULL) return;
        for (step = 1; step < n_steps; ++step) {
            data = func(shared_data, step, data);
            /* Upstream stops a pipeline when an intermediate stage returns
             * NULL. The final output stage normally returns NULL. */
            if (data == NULL && step < n_steps - 1) return;
        }
    }
}
