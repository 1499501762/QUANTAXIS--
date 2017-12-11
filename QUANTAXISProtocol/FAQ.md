# QUANTAXIS FAQ

1. QUANTAXIS web部分下载后npm install 安装失败/报错/警告

```npm install ``` 命令访问的是国外服务器 有时候会出现下载速度较慢的问题,解决方案:

```
npm install cnpm -g # 需要有管理员权限
cnpm install
```

如果安装时出现 ```fsevent```的warning 无需担心 这个是MAC平台下的npm包,windows/linux均会有此waring


如果你的nodejs版本>9 请降级你的nodejs版本 一般 安装8系列的nodejs都不会有问题


2. QUANTAXIS web 无法启动

首先检查是否8080端口被占用

``` 
netstat -ano | findstr 8080
tasklist | findstr 8080
```

其次 IE浏览器和基于IE内核的(包括且不限于)360浏览器,QQ浏览器均无法正常支持, 请替换为chrome以及基于chrome内核的浏览器版本打开

最后 如果在阿里云上部署了QUANTAXISWEB 无法打开远程地址的8080均为正常现象,请尽量本地部署


3. QUANTAXIS web 启动后点击登录/注册按钮无反应

首先检查是否进行了初始化的```save all / save x ```命令 该命令会在数据库中创建一个admin的用户

其次检查```localhost:3000```端口是否开启,及backend部分代码是否开启

开启backend的代码是

```
cd QUANTAXIS\QUANTAXIS_Webkit\backend
forever start bin\www
```

