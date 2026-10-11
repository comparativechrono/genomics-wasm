/* Range checks for LoFreq's probability-to-PHRED path, without synthetic reads. */
#include <errno.h>
#include <fenv.h>
#include <float.h>
#include <math.h>
#include <stdio.h>
#ifdef LOFREQ_PORTABLE_NUMERICS
#include "portable_math.h"
#endif

int main(void)
{
    volatile long double inputs[] = {-1, -100, -1000, -11000, -11356, -11357, -11400, -12000};
    printf("{\"mantissaBits\":%d,\"minExponent\":%d,\"cases\":[", LDBL_MANT_DIG, LDBL_MIN_EXP);
    for (unsigned i = 0; i < sizeof(inputs)/sizeof(inputs[0]); ++i) {
        long double x = inputs[i];
        errno = 0;
#ifdef LOFREQ_PORTABLE_NUMERICS
        lofreq_math_clear();
        long double raw = lofreq_expl(x);
        int failed = errno || lofreq_math_failed();
#else
        feclearexcept(FE_ALL_EXCEPT);
        long double raw = expl(x);
        int failed = errno || fetestexcept(FE_INVALID | FE_DIVBYZERO | FE_OVERFLOW | FE_UNDERFLOW);
#endif
        long double probability = raw;
        if (failed) probability = probability < DBL_EPSILON ? LDBL_MIN : LDBL_MAX;
        long double log10_probability = log10l(probability);
        printf("%s{\"x\":\"%.0Lf\",\"rawProbability\":\"%.24Le\",\"probability\":\"%.24Le\","
               "\"positive\":%s,\"rangeError\":%s,\"log10\":\"%.24Le\",\"phred\":%d}",
               i ? "," : "", x, raw, probability, probability > 0 ? "true" : "false",
               failed ? "true" : "false", log10_probability, (int)(-10.0L * log10_probability));
    }
    printf("],\"tailCases\":[");
    volatile double tail_inputs[][2] = {{-1, -2}, {-1, -1000}, {-1000, -11000}};
    for (unsigned i = 0; i < sizeof(tail_inputs)/sizeof(tail_inputs[0]); ++i) {
        double a = tail_inputs[i][0], b = tail_inputs[i][1];
        errno = 0;
#ifdef LOFREQ_PORTABLE_NUMERICS
        lofreq_math_clear();
        double tail = a + lofreq_log1p(lofreq_exp(b - a));
        long double raw = lofreq_expl(tail);
        int failed = errno || lofreq_math_failed();
#else
        feclearexcept(FE_ALL_EXCEPT);
        double tail = a + log1p(exp(b - a));
        long double raw = expl(tail);
        int failed = errno || fetestexcept(FE_INVALID | FE_DIVBYZERO | FE_OVERFLOW | FE_UNDERFLOW);
#endif
        long double probability = raw;
        if (failed) probability = probability < DBL_EPSILON ? LDBL_MIN : LDBL_MAX;
        long double log10_probability = log10l(probability);
        printf("%s{\"a\":\"%.0f\",\"b\":\"%.0f\",\"rawProbability\":\"%.24Le\","
               "\"probability\":\"%.24Le\",\"positive\":%s,\"rangeError\":%s,"
               "\"log10\":\"%.24Le\",\"phred\":%d}",
               i ? "," : "", a, b, raw, probability, probability > 0 ? "true" : "false",
               failed ? "true" : "false", log10_probability, (int)(-10.0L * log10_probability));
    }
    puts("]}");
    return 0;
}
