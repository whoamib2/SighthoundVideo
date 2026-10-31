"""MoveVideoDialog progress gauge must receive ints (wx 4)."""
from frontEnd.MoveVideoDialog import _MoveVideoProgressDialog


def test_set_bytes_passes_int_to_gauge():
    values = []

    class _Gauge:
        def SetValue(self, value):
            if not isinstance(value, int):
                raise TypeError("Gauge.SetValue(): float %r" % (value,))
            values.append(value)

    class _Label:
        def SetLabel(self, s):
            pass

    class _Fake:
        _copying = True
        _targetSize = 3 * 1024 ** 3
        _startTime = 0.0
        _gauge = _Gauge()
        _estimatedTime = _Label()

        def Refresh(self):
            pass

    def call(dlg, n):
        try:
            _MoveVideoProgressDialog._setBytes(dlg, n)
        except AttributeError:
            pass  # later ETA bookkeeping isn't faked; the gauge call comes first

    dlg = _Fake()
    call(dlg, 0)
    call(dlg, 1024 ** 3)
    assert values[:2] == [0, 33]
    dlg._targetSize = 0
    call(dlg, 5)
    assert values[-1] == 0
