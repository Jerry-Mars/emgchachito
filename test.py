# class RealtimeEMGSimulator:
#     def __init__(self, config, protocol, n_channels=1):
#         self.cfg = config
#         self.protocol = protocol
#         self.n_channels = n_channels
#         self.rng = np.random.default_rng(config.seed)

#         self.sample_index = 0
#         self.a_prev = np.zeros(n_channels)

#         self.sos = signal.butter(
#             config.filter_order,
#             [config.lowcut / (config.fs / 2), config.highcut / (config.fs / 2)],
#             btype="bandpass",
#             output="sos"
#         )

#         self.zi = np.zeros(
#             (n_channels, self.sos.shape[0], 2)
#         )

#         self.powerline_phase = self.rng.uniform(0, 2*np.pi, size=n_channels)

#     def step(self, chunk_size):
#         fs = self.cfg.fs
#         idx = np.arange(chunk_size) + self.sample_index
#         t = idx / fs

#         # 1. 根据实验协议得到命令和标签
#         u_chunk, label_chunk = self.protocol.command(t)

#         # 2. 逐点更新激活动力学
#         a_chunk = np.zeros_like(u_chunk)
#         for n in range(chunk_size):
#             tau = np.where(u_chunk[n] > self.a_prev,
#                            self.cfg.tau_on,
#                            self.cfg.tau_off)
#             alpha = np.exp(-(1/fs) / tau)
#             self.a_prev = alpha * self.a_prev + (1 - alpha) * u_chunk[n]
#             a_chunk[n] = self.a_prev

#         # 3. 激活映射到 RMS
#         sigma = self.cfg.rms_rest + (
#             self.cfg.rms_max - self.cfg.rms_rest
#         ) * (a_chunk ** self.cfg.gamma)

#         # 4. 生成带通随机载波
#         w = self.rng.standard_normal((chunk_size, self.n_channels))
#         eta = np.zeros_like(w)

#         for ch in range(self.n_channels):
#             eta[:, ch], self.zi[ch] = signal.sosfilt(
#                 self.sos,
#                 w[:, ch],
#                 zi=self.zi[ch]
#             )

#         # 5. 用预先标定的 RMS 系数归一化
#         eta = eta / self.cfg.carrier_rms_calibration

#         x_clean = sigma * eta

#         # 6. 加噪声、工频、漂移、伪迹、量化
#         x = self.apply_hardware_model(x_clean, t)

#         self.sample_index += chunk_size

#         return {
#             "t": t,
#             "x": x,
#             "label": label_chunk,
#             "activation": a_chunk,
#             "sigma": sigma,
#         }

# import dearpygui.dearpygui as dpg

# dpg.create_context()

# with dpg.window(label="Demo"):
#     dpg.add_text("Hello")

# dpg.create_viewport(title="App", width=600, height=400)
# dpg.setup_dearpygui()
# dpg.show_viewport()

# while dpg.is_dearpygui_running():
#     # 每一帧执行你的逻辑
#     # 比如更新数据、处理后台状态、刷新图表等

#     dpg.render_dearpygui_frame()

# dpg.destroy_context()

