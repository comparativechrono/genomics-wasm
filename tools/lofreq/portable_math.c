#include <float.h>
#include <math.h>
#include "portable_math.h"

#ifdef __EMSCRIPTEN__
_Static_assert(LDBL_MANT_DIG == 113 && LDBL_MAX_EXP == 16384,
               "LoFreq's OpenLibm ld128 build requires IEEE binary128 long double");
#else
_Static_assert(LDBL_MANT_DIG == 64 && LDBL_MAX_EXP == 16384,
               "LoFreq's native comparison requires x86 extended-precision long double");
#endif

static int math_error;

void lofreq_math_clear(void) { math_error = 0; }
int lofreq_math_failed(void) { return math_error; }

double lofreq_exp(double x)
{
    double result = exp(x);
    if (isfinite(x) && (!isfinite(result) || result < DBL_MIN))
        math_error = 1;
    return result;
}

long double lofreq_expl(long double x)
{
    long double result = expl(x);
    if (isfinite(x) && (!isfinite(result) || result < LDBL_MIN))
        math_error = 1;
    return result;
}

double lofreq_log1p(double x)
{
    double result = log1p(x);
    if (x <= -1 || (isfinite(x) && x != 0 &&
                    (!isfinite(result) || fabs(result) < DBL_MIN)))
        math_error = 1;
    return result;
}
