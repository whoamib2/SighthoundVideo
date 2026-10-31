"""Tiny ONNX graphs for the attribute-recognizer tests.

These graphs run in ONNX Runtime. They are not plate, face, or carrier
models. CI uses them so it does not download weights.
"""

from __future__ import annotations

import os


def _save(path: str, model) -> str:
    import onnx

    onnx.checker.check_model(model)
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    onnx.save(model, path)
    return path


def _meta(model, key: str, value: str) -> None:
    prop = model.metadata_props.add()
    prop.key = key
    prop.value = value


def build_constant_box_model(path: str, output_name: str = "plate", size: int = 32) -> str:
    """A box covering the whole network input, with a high score."""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    def tensor(name, array):
        return numpy_helper.from_array(np.asarray(array, dtype=np.float32), name)

    initializers = [
        tensor("x1", np.array([0.0], dtype=np.float32)),
        tensor("y1", np.array([0.0], dtype=np.float32)),
        tensor("x2", np.array([float(size)], dtype=np.float32)),
        tensor("y2", np.array([float(size)], dtype=np.float32)),
        tensor("score0", np.array([0.99], dtype=np.float32)),
        tensor("zero_scalar", np.array([0.0], dtype=np.float32)),
    ]
    nodes = [
        helper.make_node("ReduceMean", ["images"], ["mean"], axes=[1, 2, 3], keepdims=0),
        helper.make_node("Mul", ["mean", "zero_scalar"], ["ignored"]),
        helper.make_node("Add", ["score0", "ignored"], ["score"]),
        helper.make_node("Concat", ["x1", "y1", "x2", "y2", "score"], [output_name], axis=0),
    ]
    graph = helper.make_graph(
        nodes,
        "sighthound_constant_box",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, size, size])],
        [helper.make_tensor_value_info(output_name, TensorProto.FLOAT, [5])],
        initializers,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 11)])
    model.ir_version = 8
    _meta(model, "sighthound_layout", "box")
    return _save(path, model)


def build_strip_ocr_model(path: str, height: int = 16, width: int = 64, slots: int = 8) -> str:
    """Each horizontal slot's mean pixel value is an ASCII code."""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    if width % slots != 0:
        raise ValueError("width must divide into slots")
    slot_w = width // slots
    shape = numpy_helper.from_array(
        np.asarray([1, height, slots, slot_w], dtype=np.int64), "shape"
    )
    nodes = [
        helper.make_node("ReduceMean", ["images"], ["gray"], axes=[1], keepdims=1),
        helper.make_node("Reshape", ["gray", "shape"], ["blocks"]),
        helper.make_node("ReduceMean", ["blocks"], ["means"], axes=[1, 3], keepdims=0),
        helper.make_node("Squeeze", ["means"], ["chars"], axes=[0]),
    ]
    graph = helper.make_graph(
        nodes,
        "sighthound_strip_ocr",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, height, width])],
        [helper.make_tensor_value_info("chars", TensorProto.FLOAT, [slots])],
        [shape],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 11)])
    model.ir_version = 8
    _meta(model, "sighthound_layout", "strip-ocr")
    return _save(path, model)


def build_strip_embed_model(path: str, size: int = 32, slots: int = 8) -> str:
    """An 8-D embedding from vertical strips of the image."""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    if size % slots != 0:
        raise ValueError("size must divide into slots")
    slot_w = size // slots
    shape = numpy_helper.from_array(
        np.asarray([1, size, slots, slot_w], dtype=np.int64), "shape"
    )
    nodes = [
        helper.make_node("ReduceMean", ["images"], ["gray"], axes=[1], keepdims=1),
        helper.make_node("Reshape", ["gray", "shape"], ["blocks"]),
        helper.make_node("ReduceMean", ["blocks"], ["means"], axes=[1, 3], keepdims=0),
        helper.make_node("Squeeze", ["means"], ["embedding"], axes=[0]),
    ]
    graph = helper.make_graph(
        nodes,
        "sighthound_strip_embed",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, size, size])],
        [helper.make_tensor_value_info("embedding", TensorProto.FLOAT, [slots])],
        [shape],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 11)])
    model.ir_version = 8
    _meta(model, "sighthound_layout", "embedding")
    return _save(path, model)


def build_carrier_logits_model(path: str, size: int = 32) -> str:
    """Bright images score as UPS. Dark images score as none.

    Logit order is none, USPS, FedEx, UPS, Amazon.
    """
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    weight = np.array([[-1.0, -1.0, -1.0, 2.0, -1.0]], dtype=np.float32)
    bias = np.array([0.2, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
    scale = np.array([255.0], dtype=np.float32)
    initializers = [
        numpy_helper.from_array(weight, "W"),
        numpy_helper.from_array(bias, "B"),
        numpy_helper.from_array(scale, "scale"),
    ]
    nodes = [
        helper.make_node("ReduceMean", ["images"], ["mean"], axes=[1, 2, 3], keepdims=0),
        helper.make_node("Unsqueeze", ["mean"], ["mean1"], axes=[0]),
        helper.make_node("Div", ["mean1", "scale"], ["brightness"]),
        helper.make_node("Gemm", ["brightness", "W", "B"], ["logits"]),
    ]
    graph = helper.make_graph(
        nodes,
        "sighthound_carrier_logits",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, size, size])],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1, 5])],
        initializers,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 11)])
    model.ir_version = 8
    _meta(model, "sighthound_layout", "carrier-logits")
    return _save(path, model)
