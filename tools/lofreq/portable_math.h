#ifndef LOFREQ_PORTABLE_MATH_H
#define LOFREQ_PORTABLE_MATH_H
/* Observe the exp/expl/log1p operations used between LoFreq's four numeric
 * error checkpoints. WebAssembly has no hardware floating-point flags. */
void lofreq_math_clear(void);
int lofreq_math_failed(void);
double lofreq_exp(double x);
long double lofreq_expl(long double x);
double lofreq_log1p(double x);
#endif
