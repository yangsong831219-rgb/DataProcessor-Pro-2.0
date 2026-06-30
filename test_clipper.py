"""临时测试脚本 — 验证 WebClipper 微信图文抓取 + 图片本地化 + 入库"""
import sys
sys.path.insert(0, ".")

from dp_engine.web_clipper import WebClipper

# ⚠️ 替换为你要测试的微信公众号文章链接
TEST_URL = "https://mp.weixin.qq.com/s?chksm=c0ae2956f7d9a04038b3f0c01b3b49e901758b9625e85fa94250a1dcd2bab6c55746cfb71d81&exptype=timeline_unsubscribed_oftenread_card&req_id=1779920533061656&mid=2247489803&sn=c436fd99c369203c98c0516585e5afd0&idx=1&__biz=MzkwMTc4NTkwNg%3D%3D&scene=169&subscene=90&sessionid=1779921616&flutter_pos=0&clicktime=1779921618&enterid=1779921618&finder_biz_enter_id=5&ranksessionid=1779920533&jumppath=1001_1779920195196%2C1101_1779920198540%2C1001_1779920533142%2C50094_1779921616506&jumppathdepth=4&ascene=56&fasttmpl_type=0&fasttmpl_fullversion=8276019-zh_CN-zip&fasttmpl_flag=0&realreporttime=1779921618701&devicetype=android-36&version=28004750&nettype=WIFI&abtest_cookie=AAACAA%3D%3D&lang=zh_CN&session_us=gh_3db42dab3122&countrycode=GI&exportkey=n_ChQIAhIQ5mi9R2J%2BgEhf5S1vYgHdmhLqAQIE97dBBAEAAAAAAB9%2BGLMutlwAAAAOpnltbLcz9gKNyK89dVj0Km7QhRvl%2F7pQvR4AKSxLG8PFnmOOHtPfCeNBqZQBZNxr5YkoUuPk5nHYnm67A6AXYuspYYKWTIqG3fGK103DaGpp6nLVfb8wIgLfAhIXryEpl2pfjrCiCPt8Z5hDPUTpQKjUmxDzRNNgi%2FZp7sw8n%2FwuhZVMZDa2x41avhscNQaldJDiEhHNvpD3pi%2Fklu4oIwxq9cIdMTl3hvDeEaPrjIS%2FJQBDtmtijig4KttyWF10VgldfvHOCl0TM2A7UbSdFCc9RQ%3D%3D&pass_ticket=AC9Xn%2FEeCD2dzEzXTpceIVqIzTTTreNjTlGqkHNbBiToM0eGbRqrdGnUgb04CGJ5&wx_header=3"

clipper = WebClipper()
title, saved_path = clipper.clip(TEST_URL, download_images=True)

print(f"标题: {title}")
print(f"保存路径: {saved_path}")
