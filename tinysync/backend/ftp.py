from .abc import Backend as BaseClass
from pathlib import PurePosixPath 
import logging 
import sys 
import time 
import datetime
from .abc import pathjoin,remindPipInstall
from ftplib import FTP,error_perm
import os 





class Backend(BaseClass):

    def __init__(self,dirPath:str,host:str,username:str, password:str,port=2121):

        super().__init__()
        self._url = f"{host}:port"
        self._dirPath = dirPath
        self._ftp = FTP() 
        self._ftp.connect(host=host, port=port) 
        self._ftp.login(user=username, passwd=password)  # 登录认证

    def __del__(self):
        self._ftp.quit()


    def getSyncPath(self)->str:
        """print path position 

        Returns:
            str: _description_
        """        
        return f"ftp:{self._url}:"+self._dirPath

    def _getValidPath(self,rpath):
        dirPath = PurePosixPath(self._dirPath)
        absPath = (dirPath / rpath).as_posix()
        if not absPath.startswith("/"):
            absPath = "/" + absPath
        return absPath



    def mkdir(self,rpath:str)->int:
        """ mkdir

        Args:
            rpath (str): path


        Returns:
            int: error code, =0 for success
        """   
        p = self._getValidPath(rpath)
        try:
            self._ftp.mkd(p)  # 尝试创建目录
            return 0 
        except Exception as e:
            logging.error(f"error={str(e)}")
            return -1

    def rmdir(self,rPathRemote:str)->int: 
        """remove a empty dir. 
        if possible, try to concrete method [purge]

        Args:
            rPathRemote (str): relative path of a remote place 
        Returns:
            int: 0 -> success,  -1 -> file not exist. For some backends, return 0 for non-exist file deleting is also work!
        """
        try:
            p = self._getValidPath(rPathRemote) 
            self._ftp.rmd(p) 
            return 0 
        except error_perm as e:
            if '550' in str(e):  # 550 No such file or directory
                return -1 
            else:
                return -99
        except Exception as e:  # 捕获其他可能的错误
            return -99 
         


    def listPath(self,rpath:str,mtime:bool=True,hash:bool=False)->list[ dict ]:
        """list files and directories (not recursively) in a given (relative) path. 

        Args:
            rpath (_type_): relative path , example: rpath = "this/path", rpath = "" 
            mtime (bool, optional): contains mtime or not.
            hash (bool, optional): contains hash value or not.

        Returns:
            list[ dict ]: dict = { "Size":int, "mtime":float, 'type':'d'/'f'/'l',  'target':str (for link case), 'ltype':'d'/'f' (for link case)  }
            if hash, dict should contain dict["Hashes"] = {...}, for example dict["Hashes"]={"sha1":"66c..."}
        """         
        absPath = self._getValidPath(rpath)
        self._ftp.cwd(absPath) 
        files = self._ftp.mlsd()
        res = []
        for file, facts in files:
            name = file
            Size = int(facts['size']) 
            mtime = datetime.datetime.strptime(facts['modify'], '%Y%m%d%H%M%S').replace(tzinfo=datetime.timezone.utc).timestamp() 
            info = {
                'name':name,
                'Size':Size, 
                'mtime':int(mtime),
                'type': 'd' if facts['type'] == 'dir' else 'f',
            }
            res.append(info)
        return res 
    
            

    def putFile(self,localPath:str,rPathRemote:str): 
        """a local file <localPath> is uploading to remote at be <rPathRemote>

        Args:
            localPath (str): abs-path of a local path
            rPathRemote (str): relative path of a remote place 
        """     
        rPathAbs = self._getValidPath( rPathRemote )
        with open(localPath, 'rb') as local_file:
            self._ftp.storbinary(f'STOR {rPathAbs}', local_file)
        try:
            local_mtime = os.path.getmtime(localPath) # 获取本地文件的修改时间
            self._ftp.sendcmd(f'SITE UTIME {rPathAbs} {local_mtime}') # 使用 SITE UTIME 修改远程文件的修改时间（如果 FTP 服务器支持）
        except Exception as e:
            pass  



    def getFile(self,rPathRemote:str,localPath:str)->int: 
        """download a remote file <rPathRemote> to be local file <localPath> 

        Args:
            rPathRemote (str): relative path of a remote place 
            localPath (str): abs-path of a local path
        Returns:
            int: 0 -> success,  -1 -> file not exist
        """  
        rPathAbs = self._getValidPath( rPathRemote )
        try:
            with open(localPath, 'wb') as local_file:
                self._ftp.retrbinary(f'RETR {rPathAbs}', local_file.write)
            return 0 
        except Exception as e:
            print(str(e))
            if '551' in str(e) or '550' in str(e):
                return -1 
            else:
                return -99 


    def remoteMove(self,rPathSrc:str,rPathDst:str)->int:
        """ OPTIONAL IMPLEMENTATION 
        move a source (of file or dir) to target path

        Args:
            rPathSrc (str): source path
            rPathDst (str): target path 

        Returns:
            int: error code, 0
        """        
        old_path = self._getValidPath(rPathSrc)
        new_path = self._getValidPath(rPathDst)
        try:
            self._ftp.rename(old_path, new_path)  # 使用 FTP RENAME 命令移动文件
            return 0 
        except Exception as e:
            return -1 
    
    def deleteFile(self,rPathRemote:str): 
        """delete a remote file <rPathRemote>

        Args:
            rPathRemote (str): relative path of a remote place 
        Returns:
            int: 0 -> success,  -1 -> file not exist. For some backends, return 0 for non-exist file deleting is also work!
        """ 
        p = self._getValidPath(rPathRemote)
        try:
            self._ftp.delete(p)  # 尝试删除文件
        except error_perm as e:  # 捕获权限错误
            # 检查错误消息是否指示文件不存在
            if "550" in str(e) or "551" in str(e):
                return -1 
            else:
                return -99 
        except Exception as e:
            return -99 
        
    


