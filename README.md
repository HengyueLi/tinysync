


# TinySync
TinySync is a two-path synchronization algorithm implemented in pure Python that can synchronize files between any two kinds of **backends**. A **backend** could be a local file system, a WebDAV server, or any other user-defined backend. The synchronization algorithm is abstracted from the project [syncrclone](https://github.com/Jwink3101/syncrclone). In principle, this project is compatible with any operating system, although most testing has been conducted on Ubuntu.

# Installation 

```
pip install tinysync
```

# Examples

## Synchronization between Local Directory and WebDAV
This example demonstrates synchronizing a remote storage (WebDAV) with a local directory.


```python

import tinysync


# set a local dir
backendA = tinysync.backend.LocalFS(dirPath="/home/XXX/Desktop/p1")


# set another backend on webdav
options = {'webdav_hostname': "...",'webdav_login': "...",'webdav_password':"..."}
backendB = tinysync.backend._WebDAV(dirPath='p2',options=options)


# run sync method
tinysync.syncronization(backendA,backendB)

```

## Synchronization between Local Directory and SSH Server
In this example, files are synchronized between a local directory and a remote directory on an SSH server.

```python
import tinysync

# set one backend
config={"hostname":'...',"username":'...',"password":'...',}
remote_path = '/home/user/path'  # the remote directory to list
backendA = tinysync.backend.NixSSH(dirPath=remote_path,paramikoConfig=config)


# set another backend 
options = {'webdav_hostname':"...",'webdav_login':"...",'webdav_password':"...",}
backendB = tinysync.backend._WebDAV(dirPath='p2',options=options)


# run sync method
tinysync.syncronization(backendA,backendB)

```



## Synchronization between Any Two Backends
This functionality allows synchronization between any two remote storage locations. The machine running this code acts as a "working machine" and retains no information about the two backends. All state information is stored on the backend side.


# User-defined Backend
For general cases, you can define your own backend interface by creating a new backend class. Detailed methods and implementation examples can be found in `tinysync.backend.abc`


```python

from tinysync.backend.abc import Backend

class YourBackend(Backend):
    
    def __init__(...):
        ...


    def listPath(self,...):
        ...

    ...

```




# Currently Implemented Backends
- `tinysync.backend.LocalFS`
- `tinysync.backend._WebDAV`
- `tinysync.backend.NixSSH`

more ...



