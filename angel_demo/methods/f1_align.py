"""Alias module so eval/f3_compare's default `F3_EVAL_F1_ALIGN=methods.f1_align:align` resolves
to the F1 aligner without configuration. The implementation lives in methods/f1_alignment/."""

from angel_demo.methods.f1_alignment import align, align_async, align_network, submit_align  # noqa: F401
