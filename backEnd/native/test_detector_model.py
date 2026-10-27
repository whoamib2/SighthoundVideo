"""Build a tiny ONNX detector used by the tests.

The graph is a real ONNX model: it thresholds the image, finds the bright
pixel bounds, and emits one ``person`` box. It is not a substitute for
YOLOX-Nano. Tests use it so CI does not download weights, and so a synthetic
video has a detector that agrees with the motion tracker.

Output row is ``x1, y1, x2, y2, score, class`` in the network's input pixels.
``x2`` and ``y2`` are exclusive. Class 0 is ``person``.
"""

from __future__ import annotations

import os

_INPUT = 64


def build_bright_blob_model(path: str, size: int = _INPUT) -> str:
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    def tensor(name: str, array) -> onnx.TensorProto:
        return numpy_helper.from_array(np.asarray(array, dtype=np.float32), name)

    coords = np.arange(size, dtype=np.float32)
    initializers = [
        tensor("half", np.array(0.5, dtype=np.float32)),
        tensor("one", np.ones((1, 1, 1, 1), dtype=np.float32)),
        tensor("score_scale", np.array(0.95, dtype=np.float32)),
        tensor("class_id", np.array(0.0, dtype=np.float32)),
        tensor("big", np.array(float(size), dtype=np.float32)),
        tensor("coords_x", coords.reshape(1, 1, 1, size)),
        tensor("coords_y", coords.reshape(1, 1, size, 1)),
    ]
    nodes = [
        helper.make_node("ReduceMean", ["images"], ["gray"], axes=[1], keepdims=1),
        helper.make_node("Greater", ["gray", "half"], ["mask_bool"]),
        helper.make_node("Cast", ["mask_bool"], ["mask"], to=TensorProto.FLOAT),
        helper.make_node("ReduceMax", ["mask"], ["col_pres"], axes=[2], keepdims=1),
        helper.make_node("ReduceMax", ["mask"], ["row_pres"], axes=[3], keepdims=1),
        helper.make_node("ReduceMax", ["mask"], ["any_bright"], axes=[2, 3], keepdims=1),
        helper.make_node("Mul", ["col_pres", "coords_x"], ["col_weighted"]),
        helper.make_node("Sub", ["one", "col_pres"], ["col_absent"]),
        helper.make_node("Mul", ["col_absent", "big"], ["col_absent_big"]),
        helper.make_node("Add", ["col_weighted", "col_absent_big"], ["col_for_min"]),
        helper.make_node("ReduceMin", ["col_for_min"], ["x1"], axes=[3], keepdims=0),
        helper.make_node("ReduceMax", ["col_weighted"], ["x2_idx"], axes=[3], keepdims=0),
        helper.make_node("Mul", ["row_pres", "coords_y"], ["row_weighted"]),
        helper.make_node("Sub", ["one", "row_pres"], ["row_absent"]),
        helper.make_node("Mul", ["row_absent", "big"], ["row_absent_big"]),
        helper.make_node("Add", ["row_weighted", "row_absent_big"], ["row_for_min"]),
        helper.make_node("ReduceMin", ["row_for_min"], ["y1"], axes=[2], keepdims=0),
        helper.make_node("ReduceMax", ["row_weighted"], ["y2_idx"], axes=[2], keepdims=0),
        helper.make_node("Mul", ["any_bright", "score_scale"], ["score_box"]),
        helper.make_node("Squeeze", ["x1"], ["x1s"]),
        helper.make_node("Squeeze", ["y1"], ["y1s"]),
        helper.make_node("Squeeze", ["x2_idx"], ["x2s_idx"]),
        helper.make_node("Squeeze", ["y2_idx"], ["y2s_idx"]),
        helper.make_node("Squeeze", ["score_box"], ["score"]),
        helper.make_node("Add", ["x2s_idx", "one_scalar"], ["x2s"]),
        helper.make_node("Add", ["y2s_idx", "one_scalar"], ["y2s"]),
        helper.make_node("Unsqueeze", ["x1s"], ["x1u"], axes=[0]),
        helper.make_node("Unsqueeze", ["y1s"], ["y1u"], axes=[0]),
        helper.make_node("Unsqueeze", ["x2s"], ["x2u"], axes=[0]),
        helper.make_node("Unsqueeze", ["y2s"], ["y2u"], axes=[0]),
        helper.make_node("Unsqueeze", ["score"], ["score_u"], axes=[0]),
        helper.make_node("Unsqueeze", ["class_id"], ["class_u"], axes=[0]),
        helper.make_node(
            "Concat",
            ["x1u", "y1u", "x2u", "y2u", "score_u", "class_u"],
            ["row"],
            axis=0,
        ),
        helper.make_node("Unsqueeze", ["row"], ["row1"], axes=[0]),
        helper.make_node("Unsqueeze", ["row1"], ["detections"], axes=[0]),
    ]
    initializers.append(tensor("one_scalar", np.array(1.0, dtype=np.float32)))
    graph = helper.make_graph(
        nodes,
        "sighthound_bright_blob",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, size, size])],
        [helper.make_tensor_value_info("detections", TensorProto.FLOAT, [1, 1, 6])],
        initializers,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 11)])
    model.ir_version = 8
    meta = model.metadata_props.add()
    meta.key = "sighthound_layout"
    meta.value = "xyxy"
    meta = model.metadata_props.add()
    meta.key = "sighthound_classes"
    meta.value = "sighthound"
    meta = model.metadata_props.add()
    meta.key = "sighthound_normalize"
    meta.value = "1"
    meta = model.metadata_props.add()
    meta.key = "sighthound_resize"
    meta.value = "nearest"
    meta = model.metadata_props.add()
    meta.key = "sighthound_pad"
    meta.value = "0"
    onnx.checker.check_model(model)
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    onnx.save(model, path)
    return path
