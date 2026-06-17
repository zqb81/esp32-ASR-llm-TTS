'''
深圳市普中科技有限公司（PRECHIN 普中）
技术支持：www.prechin.net
PRECHIN
 普中

实验名称：RGB彩灯实验
接线说明：RGB彩灯模块-->ESP32 IO
         WS-->(16)
         
实验现象：程序下载成功后，RGB彩灯循环点亮且循环变化颜色
         
注意事项：

'''

#导入Pin模块
from machine import Pin
from neopixel import NeoPixel
import time


#定义RGB控制对象
#控制引脚为16，RGB灯串联5个
pin=16
rgb_num=5
rgb_led=NeoPixel(Pin(pin,Pin.OUT),rgb_num)  

#定义RGB颜色
RED = (255, 0, 0)
ORANGE = (255, 165, 0)
YELLOW = (255, 150, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
INDIGO = (75, 0, 130)
VIOLET = (138, 43, 226)
COLORS = (RED, ORANGE, YELLOW, GREEN, BLUE, INDIGO, VIOLET)

#程序入口
if __name__=="__main__":
    
    while True:
        for color in COLORS:
            for i in range(rgb_num):
                rgb_led[i]=(color[0], color[1], color[2])
                rgb_led.write()
                time.sleep_ms(100)
            time.sleep_ms(1000)
