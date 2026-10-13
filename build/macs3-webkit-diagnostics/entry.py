import sys,runpy,time
if sys.argv[1:2]==['--probe']:
    sys.argv=sys.argv[1:]
    runpy.run_path('/work/probe.py',run_name='__main__')
else:
    import numpy as np
    import MACS3.Signal.PileupV2 as pileup
    def instrument(label,original):
        def wrapped(*args,**kwargs):
            details=[('array',x.shape,str(x.dtype)) if isinstance(x,np.ndarray) else repr(x) for x in args]
            print('PROBE ENTER',label,details,kwargs,flush=True)
            start=time.monotonic()
            result=original(*args,**kwargs)
            print('PROBE RETURN',label,'seconds',time.monotonic()-start,flush=True)
            return result
        return wrapped
    for name in ['naive_quick_pileup','naive_call_peaks','pileup_from_PN_shifted']:
        setattr(pileup,name,instrument('PileupV2.'+name,getattr(pileup,name)))
    for name in ['correlate','convolve']:
        setattr(np,name,instrument('numpy.'+name,getattr(np,name)))
    print('PROBE entering unchanged upstream CLI',sys.argv,flush=True)
    runpy.run_path('/work/upstream-macs3.py',run_name='__main__')
