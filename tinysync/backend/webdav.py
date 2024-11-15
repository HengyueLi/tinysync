from .abc import Backend as BaseClass
from pathlib import Path 
import logging 
import sys 
import time 
import datetime

from webdav3.client import Client,RemoteResourceNotFound
from .abc import pathjoin



class Backend(BaseClass):

    def __init__(self,dirPath:str,options:dict):
        super().__init__()
        self._dirPath = dirPath
        self._client = Client(options)
        hostname = "/".join(options['webdav_hostname'].split("//")[1:])
        if hostname.endswith("/"):
            self.hostname = hostname[:-1]
        else:
            self.hostname = hostname   

    def getSyncPath(self)->str:
        """print path position 

        Returns:
            str: _description_
        """        
        return "webdav:"+self._dirPath

    def _getValidPath(self,rpath):
        return pathjoin( self._dirPath, rpath )


    def _list(self,rpath:str):
        try:
            p = self._getValidPath(rpath)
            items = self._client.list(p, get_info=True) 
            return items
        except:
            return None

    # def getMetadata(self,rpath:str)->None|dict:
    #     """get the meta data of a path

    #     Args:
    #         rpath (str): relative path 


    #     Returns:
    #         None|dict: if path not exsists, return None else return { 'type':'d'/'f'/'l', 'Size':int, 'mtime':int (seconds), 'target':str (for link case), 'ltype':'d'/'f' (for link case) , 'Hashes'(optional):{}  } 
    #     """  
    #     items = self._list(rpath) 
    #     if items is None:
    #         return None
    #     try:
    #         this = items[0]
    #         Size =  0 if this['isdir'] else this['size']
    #         return {
    #             'type':'d' if this['isdir'] else 'f', 
    #             'mtine':time.mktime(datetime.datetime.strptime(this['modified'], '%a, %d %b %Y %H:%M:%S %Z').timetuple()),
    #             'Size':Size,
    #         }
    #     except:
    #         return None  


    def mkdir(self,rpath:str)->int:
        """ mkdir

        Args:
            rpath (str): path


        Returns:
            int: error code, =0 for success
        """   
        p = self._getValidPath(rpath)
        try:
            self._client.mkdir(p) 
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
            self._client.clean(p) 
            return 0 
        except RemoteResourceNotFound:
            return -1 
        except:
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
        res = [] 
        items = self._list(rpath)
        this = items[0]
        items = items[1:]
        here:str = this['path'] # endswith "/"
        for item in items:
            name:str = item['path'].replace(here,'')
            Type = 'd' if item['isdir'] else 'f' 
            Size = int(item['size']) if Type == 'f' else 0
            if name.endswith('/'):
                  name = name[:-1]
            mt = time.mktime(datetime.datetime.strptime(item['modified'], '%a, %d %b %Y %H:%M:%S %Z').timetuple())

            if len(rpath) == 0:
                path = name 
            elif rpath.endswith("/"):
                path = rpath+name 
            else:
                path = rpath+"/"+name
            res.append({
                'name':name,
                # 'Path':path, 
                'Size':Size, 
                'mtime':mt,
                'type':Type,  
                'Hashes':{}, 
            })
        return res 
            

    def putFile(self,localPath:str,rPathRemote:str): 
        """a local file <localPath> is uploading to remote at be <rPathRemote>

        Args:
            localPath (str): abs-path of a local path
            rPathRemote (str): relative path of a remote place 
        """     
        rPathAbs = self._getValidPath( rPathRemote )
        self._client.upload_sync(remote_path=rPathAbs, local_path=localPath)

    def getFile(self,rPathRemote:str,localPath:str)->int: 
        """download a remote file <rPathRemote> to be local file <localPath> 

        Args:
            rPathRemote (str): relative path of a remote place 
            localPath (str): abs-path of a local path
        Returns:
            int: 0 -> success,  -1 -> file not exist
        """  
        try:
            rPathAbs = self._getValidPath( rPathRemote )
            self._client.download_sync(remote_path=rPathAbs, local_path=localPath)
            return 0 
        except RemoteResourceNotFound:
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
        src = self._getValidPath(  rPathSrc ) 
        dst = self._getValidPath(  rPathDst )
        try:
            self._client.move(remote_path_from=src, remote_path_to=dst)
            return 0
        except:
            return -1   
    
    def deleteFile(self,rPathRemote:str): 
        """delete a remote file <rPathRemote>

        Args:
            rPathRemote (str): relative path of a remote place 
        Returns:
            int: 0 -> success,  -1 -> file not exist. For some backends, return 0 for non-exist file deleting is also work!
        """ 
        try:
            p = self._getValidPath(rPathRemote) 
            self._client.clean(p) 
            return 0 
        except RemoteResourceNotFound:
            return -1 
        except:
            return -99 
        
    
    def purge(self, rPathRemote: str) -> int:
        return self._client.clean( self._getValidPath(rPathRemote) )
        


