from .abc import Backend as BaseClass
import os,shutil
import hashlib
import logging 
import sys 
import uuid

def file_hash_sha1(filename):
    """计算文件的 SHA-1 哈希"""
    h = hashlib.sha1()  # 创建 SHA-1 哈希对象
    with open(filename, 'rb') as file:
        while True:
            chunk = file.read(4096)  # 读取文件块（通常选择一个合适的块大小，例如 4096 字节）
            if not chunk:
                break
            h.update(chunk)  # 更新哈希对象
    return h.hexdigest()  # 返回十六进制格式的哈希值



class Backend(BaseClass):

    def __init__(self,dirPath:str):
        super().__init__()
        self._dirPath = dirPath
    
    def getSyncPath(self)->str:
        """print path position 

        Returns:
            str: _description_
        """      
        if not hasattr(self,"_GETSYNCPATH__"):
            unique_id = uuid.UUID(int=uuid.getnode()).hex  
            short = hashlib.md5(unique_id.encode()).hexdigest()[:5]
            strTag =  f"localFS({short}):"+self._dirPath 
            self._GETSYNCPATH__ = strTag 
        return self._GETSYNCPATH__



    def mkdir(self,rpath:str)->int:
        """ mkdir

        Args:
            rpath (str): path


        Returns:
            int: error code, =0 for success
        """   
        _path = os.path.join( self._dirPath, rpath ) 
        try:
            os.mkdir(_path)
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
            print("here1",os.path.join( self._dirPath, rPathRemote ) )
            os.rmdir( os.path.join( self._dirPath, rPathRemote ) )
            print("here2")
            return 0 
        except FileNotFoundError:
            return -1
        except OSError:
            # not empty
            return -2
        except:
            return -3
         


    def listPath(self,rpath:str,mtime:bool=True,hash:bool=False)->list[ dict ]:
        """list files and directories (not recursively) in a given (relative) path. 

        Args:
            rpath (_type_): relative path , example: rpath = "this/path", rpath = "" 
            mtime (bool, optional): contains mtime or not.
            hash (bool, optional): contains hash value or not.

        Returns:
            list[ dict ]: dict = { "name":str,  "Size":int, "mtime":float, 'type':'d'/'f'/'l',  'target':str (for link case), 'ltype':'d'/'f' (for link case)  }
            if hash, dict should contain dict["Hashes"] = {...}, for example dict["Hashes"]={"sha1":"66c..."}
        """         
        absPath = os.path.join( self._dirPath,  * rpath.split("/") )
        res = []
        with os.scandir(absPath) as entries:
            for entry in entries:
                # 获取文件名
                data = {"name":entry.name,'Size':'','mtime':''}
                p = os.path.join(absPath,data['name'])
                # 判断是否为文件夹
                if entry.is_symlink():
                    data['type'] = 'l'
                    data['target'] = os.readlink(p)
                    try:
                        data['ltype'] = 'd' if entry.is_dir() else 'f' 
                    except:
                        if self.symlinktype in ('d','f'):
                            data['ltype'] = self.symlinktype 
                        else:
                            raise Exception(f"self.symlinktype can only be 'd'/'f'. But you input {self.symlinktype}")
                elif entry.is_dir():
                    data['type'] = 'd'
                elif entry.is_file:
                    data['type'] = 'f'
                    data['Size'] = entry.stat().st_size
                    data['mtime'] = int(entry.stat().st_mtime) #if mtime else ""
                else:
                    data['type'] = 'unknow'
                # 获取最后修改时间
                # mtime = entry.stat().st_mtime
                if data['type'] == 'f' and hash:
                    data['Hashes'] = { "sha1":file_hash_sha1(p) } 
                res.append(data) 
        return res 

    def putFile(self,localPath:str,rPathRemote:str): 
        """a local file <localPath> is uploading to remote at be <rPathRemote>

        Args:
            localPath (str): abs-path of a local path
            rPathRemote (str): relative path of a remote place 
        """     
        rPathAbs = os.path.join( self._dirPath, rPathRemote )
        shutil.copy(src=localPath,dst=rPathAbs) 
        shutil.copystat(src=localPath,dst=rPathAbs)

    def getFile(self,rPathRemote:str,localPath:str)->int: 
        """download a remote file <rPathRemote> to be local file <localPath> 

        Args:
            rPathRemote (str): relative path of a remote place 
            localPath (str): abs-path of a local path
        Returns:
            int: 0 -> success,  -1 -> file not exist
        """  
        try:
            rPathAbs = os.path.join( self._dirPath, rPathRemote ) 
            shutil.copy(src=rPathAbs,dst=localPath)
            shutil.copystat(src=rPathAbs,dst=localPath)
            return 0 
        except FileNotFoundError:
            return -1 
        except Exception as e:
            # 通用异常，用于捕获上述异常之外的其他异常
            logging.error(f"error = {str(e)}")
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
        src = os.path.join( self._dirPath, rPathSrc ) 
        dst = os.path.join( self._dirPath, rPathDst )
        dstDir = os.path.dirname(dst) 
        if not os.path.exists(dstDir):
            os.makedirs(dstDir)
        os.rename(src,dst) 
        return 0  
    
    def deleteFile(self,rPathRemote:str): 
        """delete a remote file <rPathRemote>

        Args:
            rPathRemote (str): relative path of a remote place 
        Returns:
            int: 0 -> success,  -1 -> file not exist. For some backends, return 0 for non-exist file deleting is also work!
        """ 
        try:
            rPathAbs = os.path.join( self._dirPath, rPathRemote ) 
            os.remove(rPathAbs)
            return 0 
        except FileNotFoundError:
            return -1 
        except:
            return -99 
        

    def mklink(self, rPathRemote: str, target: str,isDir:bool):
        """ OPTIONAL IMPLEMENTATION 
        create a symbolic link 

        Args:
            rPathRemote (str): file path
            target (str): target of the link
            isDir (bool): is dir or not 
        """       
        path = os.path.join( self._dirPath, rPathRemote )
        if sys.platform == 'win32':
            if isDir:
                os.system(f'mklink /D "{path}" "{target}"')
            else:
                os.system(f'mklink "{path}" "{target}"')
        else:
            # Unix系统使用os.symlink
            os.symlink(target,path,target_is_directory=isDir)



    def purge(self,rPathRemote:str)->int: 
        """ OPTIONAL IMPLEMENTATION 
        remove a dir and all of its inside files and dirs.

        Args:
            rPathRemote (str): relative path of a remote place 
        Returns:
            int: 0 -> success
        """ 
        path = os.path.join( self._dirPath, rPathRemote ) 
        if os.path.isdir(path):
            shutil.rmtree(path) 
        else:
            os.remove(path)
