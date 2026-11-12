"""Convert google/deepvariant:1.10.0's WGS models to wgse-deepvariant's weight files.

Run inside the official image (it has TensorFlow, Keras and NumPy):

    docker run --rm -v "$PWD":/w google/deepvariant:1.10.0@sha256:... \
        python3 /w/native/deepvariant/convert_weights.py /w/out/weights

Writes <out>/wgs.dvw, the big model's float32 variables in the port's
weight-file layout (IPNP-BIPN/deepvariant tools/conversion/extract_weights.py),
and <out>/wgs_small_weights/layer_*_{kernel,bias}.npy, the small model's
dense layers. Both are deterministic: weights-sha256.txt lists their hashes.
"""

import struct
import sys
from pathlib import Path

import numpy as np

SAVED_MODEL = "/opt/models/wgs"
SMALL_MODEL = "/opt/smallmodels/wgs/model.keras"
DVW_MAGIC, DVW_VERSION, DT_FLOAT = b"DVW1", 1, 1


def write_dvw(saved_model: str, out: Path) -> None:
    import tensorflow as tf

    reader = tf.train.load_checkpoint(f"{saved_model}/variables/variables")
    names = sorted(
        n for n, d in reader.get_variable_to_dtype_map().items() if d == tf.float32
    )
    arrays = [np.ascontiguousarray(reader.get_tensor(n), dtype="<f4") for n in names]
    with out.open("wb") as f:
        f.write(DVW_MAGIC + struct.pack("<II", DVW_VERSION, len(names)))
        offset = 0
        for name, a in zip(names, arrays, strict=True):
            encoded = name.encode()
            f.write(struct.pack("<I", len(encoded)) + encoded)
            f.write(struct.pack("<BB", DT_FLOAT, a.ndim))
            f.write(b"".join(struct.pack("<I", d) for d in a.shape))
            f.write(struct.pack("<QQ", offset, a.nbytes))
            offset += a.nbytes
        for a in arrays:
            f.write(a.tobytes())


def write_small(small_model: str, out: Path) -> None:
    import keras

    out.mkdir(parents=True, exist_ok=True)
    model = keras.models.load_model(small_model, compile=False)
    for i, layer in enumerate(model.layers):
        for w in layer.weights:
            kind = "kernel" if "kernel" in w.name else "bias"
            np.save(out / f"layer_{i}_{kind}.npy", np.asarray(w.numpy()))


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    write_dvw(SAVED_MODEL, out / "wgs.dvw")
    write_small(SMALL_MODEL, out / "wgs_small_weights")


if __name__ == "__main__":
    main()
