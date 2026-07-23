"""
GPU/CPU 训练加速配置 — 所有训练脚本共用。

用法:
    from gpu_config import configure_device, create_dataset
    configure_device()
    train_ds = create_dataset(X_train, y_train, batch_size=32)

Windows 用户:
    - CPU 模式: 自动使用所有核心
    - GPU 模式: 需要 WSL2 + CUDA, 或 TensorFlow-DirectML
"""

import tensorflow as tf


def configure_device():
    """检测并配置最优计算设备 (GPU > CPU), 打印设备信息。"""
    gpus = tf.config.list_physical_devices("GPU")

    if gpus:
        # GPU 可用 — 设 memory growth 防止 OOM
        for gpu in gpus:
            tf.config.experimental.set_memory_growth(gpu, True)
        print(f"[GPU] {len(gpus)} device(s) detected, memory_growth enabled")

        # 开启混合精度 (float16 计算, float32 存储 — GPU 上 2-3x 加速)
        tf.keras.mixed_precision.set_global_policy("mixed_float16")
        print("[GPU] mixed_float16 enabled")
    else:
        # CPU 模式 — 通过环境变量设置线程数 (必须在 import TF 前设, 这里只打印)
        import os
        n_cores = os.cpu_count() or 4
        print(f"[CPU] No GPU found — {n_cores} CPU cores available")

    # 打印物理设备列表
    for dev in tf.config.list_physical_devices():
        print(f"  {dev.device_type}: {dev.name}")


def create_dataset(X, y, batch_size=32, shuffle=True, prefetch=True):
    """
    将 numpy 数组包装为高效 tf.data.Dataset。

    X: (n_samples, timesteps, features) — LSTM 输入
    y: (n_samples, horizon) 或 (n_samples, horizon, 3) — target
    batch_size: 批量大小
    shuffle: 训练时 True, 验证时 False
    prefetch: 启用预取流水线 (CPU 准备下一批时 GPU 在计算)

    返回: tf.data.Dataset, 每个元素为 (X_batch, y_batch)
    """
    ds = tf.data.Dataset.from_tensor_slices((X, y))

    if shuffle:
        ds = ds.shuffle(buffer_size=min(len(X), 10000))

    ds = ds.batch(batch_size)

    if prefetch:
        ds = ds.prefetch(tf.data.AUTOTUNE)

    return ds


def create_multi_input_dataset(
    X_price, X_item, y, batch_size=32, shuffle=True, prefetch=True
):
    """
    创建双输入 Dataset (LSTM-C 专用: 价格序列 + 物品 ID)。

    X_price: (n, 60, 15) 价格序列
    X_item:  (n, 1)       物品 ID
    y:       (n, 7)       target

    返回: tf.data.Dataset, 每个元素为 ((X_price_batch, X_item_batch), y_batch)
    """
    ds = tf.data.Dataset.from_tensor_slices(((X_price, X_item), y))

    if shuffle:
        ds = ds.shuffle(buffer_size=min(len(X_price), 10000))

    ds = ds.batch(batch_size)

    if prefetch:
        ds = ds.prefetch(tf.data.AUTOTUNE)

    return ds
