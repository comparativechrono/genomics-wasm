// Portability adapter for ExpansionHunter 5.0.0, not upstream scientific code.
// With exactly one worker, invoke each original callback in the caller.
#pragma once
#include <functional>
#include <utility>

class GenomicsSerialThread
{
public:
    template <typename Function, typename... Arguments>
    explicit GenomicsSerialThread(Function&& function, Arguments&&... arguments)
    {
        std::bind(std::forward<Function>(function), std::forward<Arguments>(arguments)...)();
    }
    void join() {}
};

namespace ctpl
{
class thread_pool
{
public:
    explicit thread_pool(int) {}
    template <typename Function, typename... Arguments>
    void push(Function&& function, Arguments&&... arguments)
    {
        std::bind(std::forward<Function>(function), 0, std::forward<Arguments>(arguments)...)();
    }
    void stop(bool) {}
};
}
