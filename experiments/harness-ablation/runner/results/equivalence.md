# Equivalence summary

Bootstrap 95% intervals (4000 resamples). The minimum detectable effect (MDE) is the interval half-width: effects smaller than it are not ruled out, they are simply not resolved by this design.

| comparison | n (a/b) | mean a | mean b | diff | 95% CI | MDE | margin | equivalent |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |
| T: M-on vs M-off (score) | 120/120 | 0.993 | 0.987 | +0.006 | [-0.007, +0.022] | 0.016 | 0.02 | no |
| LongBench: full vs all-off (score) | 20/20 | 0.700 | 0.650 | +0.050 | [-0.250, +0.350] | 0.300 | 0.10 | no |
| LongMemEval: M-on vs M-off (score) | 20/20 | 0.550 | 0.600 | -0.050 | [-0.350, +0.250] | 0.300 | 0.10 | no |

SWE-bench is reported as resolved counts by the official harness (10/12 full, 10/12 all-off); with twelve paired instances the exact interval around a zero difference spans roughly ±0.27.
