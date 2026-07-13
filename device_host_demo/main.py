import asyncio
import struct
import sys
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal, QObject, QEventLoop, QTimer
from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel, QVBoxLayout, QWidget
from PyQt5.QtWidgets import *
from bleak import BleakScanner
from bleak import BleakClient
from matplotlib import pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
import qasync

# 数据解析类
class W2Data:
    def __init__(self):

        self.emg_rms = [] # 肌电RMS值
        self.emg_raw = [] # 肌电原始信号


    # 解包函数
    def parse_data(self, data):

        if len(data) < 6:
            return

        check_byte = data[1] ^ data[2]

        # 帧头校验
        if (data[0] == 0xA5) and (data[3] == check_byte):
            # 帧长度
            frame_len = data[1] + 3
            # 判断剩于数据长度是否大于帧长度
            if frame_len <= len(data):
                # 校验帧尾
                if data[frame_len-1] == 0x5A :
                    self.frame_unpack(data)

    def frame_unpack(self, data):
        frameType = data[2]

        if frameType == 0x11:
            # 肌电RMS值
            if data[4] == 0x01:
               rms = data[17]*256 + data[18]
               self.emg_rms.append(rms)

            # 肌电原始信号
            if data[4] == 0x03 or data[4] == 0x04:
               data_num = int((data[1] - 9 - 4)/2)
               emg_v0 = struct.unpack('f', data[11:15])[0]
               temp = data[15:(15+data_num*2)]
               data_temp = struct.unpack("h"*(len(temp)//2), temp)
               # print(data_temp)
               self.emg_raw.append(emg_v0)

               factor = 3.1457 # 肌电数据系数
               if data[4] == 0x04:
                  factor = 12.5786 # 脑电数据系数

               for val in data_temp:
                   emg_v0 += val/factor
                   self.emg_raw.append(emg_v0)
               print(len(self.emg_raw))

# 指令集
class W2Cmd:

    ADDRESS_SOFTWARE = 0x02 #获取软件版本
    ADDRESS_DEVICE_NAME = 0x03 #配置或获取设备名称
    ADDRESS_POWER = 0x0B    #电量信息
    ADDRESS_EMG_START = 0x11 #开始/停止采集指令
    ADDRESS_SHUTDOWN = 0x14  #关机地址
    ADDRESS_HW_VERSION = 0x1D  #硬件版本

    @staticmethod
    def cmd_data_pack(addr,is_write,data):

        cmd = []

        # 帧头
        cmd.append(0xAA)
        # 帧长度
        cmd.append(len(data)+3)
        if is_write:
            cmd.append(0x80) # 写指令
        else:
            cmd.append(0x81) # 读指令
        # 地址
        cmd.append(addr)

        # 数据内容
        cmd += data

        # 校验码（待定）
        cmd.append(0x00)

        # 帧尾
        cmd.append(0xBB)

        # 计算校验码
        xor = 0x00
        for b in cmd[1:]:
            xor ^= b
        cmd[len(cmd)-2] = xor
        return cmd

    # 采集控制指令
    @staticmethod
    def start_collect_cmd(mode):
        data = []
        data.append(mode)
        data += np.zeros(8,dtype=np.uint8).data
        return W2Cmd.cmd_data_pack(W2Cmd.ADDRESS_EMG_START,True,data)

    # 停止采集
    @staticmethod
    def stop_collect_cmd():
        return W2Cmd.start_collect_cmd(0x00)

    # 开始采集肌电RMS信号
    @staticmethod
    def start_collect_emg_rms_cmd():
        return W2Cmd.start_collect_cmd(0x01)

    # 开始采集肌电原始信号
    @staticmethod
    def start_collect_emg_raw_cmd():
        return W2Cmd.start_collect_cmd(0x03)

    # 开始采集脑电原始信号
    @staticmethod
    def start_collect_eeg_raw_cmd():
        return W2Cmd.start_collect_cmd(0x04)

# BLE操作类
# 改连接设备的地址、通知特征、写特征等都在BLEOperation类中
class BLEOperation(QObject):

    # 定义信号
    data_received1 = pyqtSignal(dict)
    data_received2 = pyqtSignal(list)

    sensor_data = W2Data()

    def __init__(self):
        super().__init__()
        # 存储扫描到的设备的名称、地址、强度
        self.client = None
        self.devices = None
        # 用于储存解包的数据
        self.parsed_data = None
        # 需要连接设备的地址
        self.address = "31:23:04:00:00:11"
        # 接收通知的UUID
        self.notify_uuid = "0000FFF4-0000-1000-8000-00805F9B34FB"
        # 写数据的UUID
        self.write = "0000FFF3-0000-1000-8000-00805F9B34FB"
        self.collect_state = 0
        self.send_state = 0
        # self.sensor_data.emg_raw += np.linspace(0, 600000 - 1, 600000).data

    async def scan(self):
        print("正在扫描...")
        # 设备扫描
        self.devices = await BleakScanner.discover()
        # 将扫描到的设备按照强度从高到低来排
        sorted_devices = sorted(self.devices, key=lambda device: device.rssi, reverse=True)
        # 打印扫描到的所有设备
        for device in sorted_devices:
            if device.name is not None:
              if device.name.find("RunE W2") > -1 :
                print(f"Found device {device.name}: {device.address}: {device.rssi}")
        print("扫描完成")

    async def send_cmd(self,cmd):
        await self.client.write_gatt_char(self.write, bytes(cmd))

    def send_start_collect_cmd(self):
        # if self.collect_state == 0:
        self.collect_state = 1
        asyncio.ensure_future(self.send_cmd(W2Cmd.start_collect_emg_raw_cmd()))
            # self.send_cmd(W2Cmd.start_collect_emg_raw_cmd())
            # asyncio.run(self.send_cmd(W2Cmd.start_collect_emg_raw_cmd()))

    def send_stop_collect_cmd(self):
        # if self.collect_state == 1:
        self.collect_state = 0
        asyncio.ensure_future(self.send_cmd(W2Cmd.stop_collect_cmd()))
            # self.send_cmd(W2Cmd.stop_collect_cmd())
            # asyncio.run(self.send_cmd(W2Cmd.stop_collect_cmd()))

    async def connect_device(self):
        print("Connecting device")
        self.client = BleakClient(self.address)
        await self.client.connect()
        print("Connected to BLE device!")
        await self.client.start_notify(self.notify_uuid, self.data_receive_callback)
        print(f"Subscribed to notifications for {self.notify_uuid}")
        # # 设备连接
        # async with BleakClient(self.address) as self.client:
        #     print("Connected to BLE device!")
        #     # 订阅通知
        #     await self.client.start_notify(self.notify_uuid, self.data_receive_callback)
        #     print(f"Subscribed to notifications for {self.notify_uuid}")
        #     await asyncio.Future()
        #     print("ble end.....")

    # 数据回调函数
    def data_receive_callback(self, send, received_data):

        # 数据包解析
        self.sensor_data.parse_data(received_data)

        if self.collect_state == 1:
            # 肌电RMS信号
            # self.data_received2.emit(self.sensor_data.emg_rms)

            # 肌电原始信号
            self.data_received2.emit(self.sensor_data.emg_raw)

# 主线程
class MainWindow(QMainWindow, QObject):
    def __init__(self):
        super().__init__()
        self.ble_op = BLEOperation()
        self.temp = 0
        self.start_stop = 0
        self.restart = 0
        self.initUI()

    def initUI(self):

        # 界面的标题
        self.setWindowTitle('W2模块-Python示例')
        # 界面的位置和大小的设置
        self.setGeometry(100, 100, 800, 700)

        # 创建一个垂直布局
        layout = QVBoxLayout()

        # # 创建一个绘图区域（QWidget）
        self.drawing_area = QWidget(self)
        self.drawing_area.setFixedSize(10, 10)
        layout.addWidget(self.drawing_area)

        # 创建一个FigureCanvas来显示图像
        self.fig, self.axes = plt.subplots()
        self.canvas = FigureCanvas(self.fig)
        self.toolbar = NavigationToolbar(self.canvas, self)

        self.btn_scan = QPushButton('扫描设备', self)
        self.btn_scan.clicked.connect(self.scan_device)

        self.btn_conn = QPushButton('连接设备', self)
        self.btn_conn.clicked.connect(self.connect_device)

        self.btn_start = QPushButton('开始采集', self)
        self.btn_stop = QPushButton('停止采集', self)
        self.btn_stop.setGeometry(250, 650, 100, 30)

        # 添加FigureCanvas和NavigationToolbar到布局中
        layout.addWidget(self.canvas)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.btn_scan)
        layout.addWidget(self.btn_conn)
        layout.addWidget(self.btn_start)
        layout.addWidget(self.btn_stop)

        # 创建一个中心小部件并设置布局
        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

        # 设置图形和轴
        self.y_data = []
        self.x_data = []
        self.line,= self.axes.plot([],[],lw=1)
        self.axes.grid(True)
        self.t_count = 0
        self.axes.set_xlim(0,300)
        self.axes.set_ylim(-500, 500)
        self.axes.set_ylabel("uV")
        # self.axes.set_ylim(0, 200)

        self.btn_start.clicked.connect(self.ble_op.send_start_collect_cmd)
        self.btn_stop.clicked.connect(self.ble_op.send_stop_collect_cmd)

        self.update_show_timer = QTimer(self)
        self.update_show_timer.timeout.connect(self.update_show)
        self.update_show_timer.start(100)

    # 扫描设备
    def scan_device(self):
        asyncio.ensure_future(self.ble_op.scan())

    # 连接设备
    def connect_device(self):
        asyncio.ensure_future(self.ble_op.connect_device())

    # 更新显示
    def update_show(self):
        self.update_graph(self.ble_op.sensor_data.emg_raw)

    # 更新图像
    def update_graph(self, data):
        if len(data) == 0:
            return
        len0 = len(self.y_data)
        len1 = len(data)
        len_n =  len1 - len0
        if len_n > 0 :
            self.y_data += (data[len0:len1])
            self.x_data += np.linspace(len0,  len1-1, len_n).data

        length = len(self.y_data)

        if length <= 2500 :
          self.line.set_data(self.x_data, self.y_data)  # 更新曲线的数据
          self.axes.set_xlim(0, length)
        else :
          self.line.set_data(self.x_data[length - 2500:length], self.y_data[length - 2500:length])  # 更新曲线的数据
          self.axes.set_xlim(length - 2500, length)

        self.fig.canvas.draw()
        self.fig.canvas.flush_events()

async def main():
    app = QApplication(sys.argv)
    # 使用 qasync 将 asyncio 事件循环与 PyQt 集成
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    # 窗体UI
    main_window = MainWindow()
    main_window.show()

    # 启动事件循环
    with loop:
        loop.run_forever()

if __name__ == '__main__':
    asyncio.run(main())

