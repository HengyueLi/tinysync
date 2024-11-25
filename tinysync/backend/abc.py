from abc import ABC, abstractmethod
from typing import Callable
import logging
import os 
# from .localFS import Backend as class_backend_FS


class Backend():

    def __init__(self) -> None:
        # For most other remotes (e.g. S3, B2), hashes are stored by the remote
        # so there is no need to reuse them
        self.reuse_hashes = False # True

        # Because moving files has to be done with individual rclone calls, it is often more
        # efficient to disable rename tracking as a delete and copy can be more efficient for
        # lots of files. It also doesn't make sense to use renames on A or B if the remote B or A
        # doesn't support server-side copy or move.
        self.renames = True

        # Note that overlap is tested but isn't perfect in the case of alias remotes. May cause
        # issues later in the sync!  Not compatible with sync_backups
        self.workdir = None  # <remote>/.tinysyncer


        # default symbolic link type. 
        # For many cases, we can detect the symbolic link without knowning its a file or directory. 
        # Set a default type setting for this cases. 
        self.symlinktype = 'd'    # 'd'/'f' 

        # # work dir.  DONOT touch this data!
        # self._workdata = { } #    BatchDownloading:bool,  BatchUploading:bool


    @abstractmethod
    def getSyncPath(self)->str:
        """print path position 

        Returns:
            str: _description_
        """        
        raise NotImplementedError()

    
    @abstractmethod
    def listPath(self,rpath:str,mtime:bool=True,hash:bool=False)->list[ dict ]:
        """list files and directories (not recursively) in a given (relative) path. 

        Args:
            rpath (_type_): relative path , example: rpath = "this/path", rpath = "" 
            mtime (bool, optional): contains mtime or not.
            hash (bool, optional): contains hash value or not.

        Returns:
            list[ dict ]: dict = { "Size":int, "name":str,  "mtime":float, 'type':'d'/'f'/'l',  'target':str (for link case), 'ltype':'d'/'f' (for link case)  }
            if hash, dict should contain dict["Hashes"] = {...}, for example dict["Hashes"]={"sha1":"66c..."}
        """               
        raise NotImplementedError()

    @abstractmethod
    def mkdir(self,rpath:str)->int:
        """ mkdir

        Args:
            rpath (str): path


        Returns:
            int: error code, =0 for success
        """   
        raise NotImplementedError()   

    @abstractmethod
    def rmdir(self,rPathRemote:str)->int: 
        """remove a empty dir. 
        if possible, try to concrete method [purge]

        Args:
            rPathRemote (str): relative path of a remote place 
        Returns:
            int: 0 -> success,  -1 -> file not exist. For some backends, return 0 for non-exist file deleting is also work!
        """ 
        raise NotImplementedError()  

    @abstractmethod
    def putFile(self,localPath:str,rPathRemote:str): 
        """a local file <localPath> is uploading to the remote at be <rPathRemote>
        remember to keep the meta-data

        Args:
            localPath (str): abs-path of a local path
            rPathRemote (str): relative path of a remote place 
        """     
        raise NotImplementedError()

    @abstractmethod
    def getFile(self,rPathRemote:str,localPath:str)->int: 
        """download a remote file <rPathRemote> to be local file <localPath> 
        remember to keep the meta-data

        Args:
            rPathRemote (str): relative path of a remote place 
            localPath (str): abs-path of a local path
        Returns:
            int: 0 -> success,  -1 -> file not exist
        """   
        raise NotImplementedError()

    @abstractmethod
    def deleteFile(self,rPathRemote:str)->int: 
        """delete a remote file <rPathRemote>
        if the symbolic-link is supported in this backend, this method should also be able to delete it.

        Args:
            rPathRemote (str): relative path of a remote place 
        Returns:
            int: 0 -> success,  -1 -> file not exist. For some backends, return 0 for non-exist file deleting is also work!
        """ 
        raise NotImplementedError()  
    
    @abstractmethod
    def purge(self,rPathRemote:str)->int: 
        """ OPTIONAL IMPLEMENTATION 
        remove a dir and all of its inside files and dirs.

        Args:
            rPathRemote (str): relative path of a remote place 
        Returns:
            int: 0 -> success
        """ 
        raise NotImplementedError()  
    
    @abstractmethod
    def mklink(self,rPathRemote:str,target:str,isDir:bool):
        """ OPTIONAL IMPLEMENTATION 
        create a symbolic link 

        Args:
            rPathRemote (str): file path
            target (str): target of the link
            isDir (bool): is dir or not 
        """        
        raise NotImplementedError() 

    @abstractmethod
    def remoteMove(self,rPathSrc:str,rPathDst:str)->int:
        """ OPTIONAL IMPLEMENTATION 
        move a source (of file or dir) to target path

        Args:
            rPathSrc (str): source path
            rPathDst (str): target path 

        Returns:
            int: error code, 0
        """        
        raise NotImplementedError()

    @abstractmethod
    def remoteBatchMove(self,pairs:list[tuple[str,str]])->int:
        """ OPTIONAL IMPLEMENTATION 
        move a source (of file or dir) to target path

        Args:
            pairs (list[tuple[str,str]]): each item = (src,dst)

        Returns:
            int: error code, 0
        """       
        raise NotImplementedError()


    @abstractmethod
    def remoteCopy(self,rPathSrc:str,rPathDst:str)->int:
        """ OPTIONAL IMPLEMENTATION 
        copy a source (of file or dir) to target path

        Args:
            rPathSrc (str): source path
            rPathDst (str): target path 

        Returns:
            int: error code, 0
        """        
        raise NotImplementedError()

    @abstractmethod
    def remoteBatchCopy(self,pairs:list[tuple[str,str]])->int:
        """ OPTIONAL IMPLEMENTATION 
        copy a source (of file or dir) to target path

        Args:
            pairs (list[tuple[str,str]]): each item = (src,dst)

        Returns:
            int: error code, 0
        """       
        raise NotImplementedError()

    @abstractmethod
    def remoteBatchDelete(self,items:list[str])->int:
        """ OPTIONAL IMPLEMENTATION 
        batch delete remote files (only files, not dir)

        Args:
            items (list[str]): each item = file path

        Returns:
            int: error code, 0
        """       
        raise NotImplementedError()
    
    @abstractmethod
    def cleanQueue(self):
        """ OPTIONAL IMPLEMENTATION
        In some cases, to seedup download/upload, you can put missions into a queue to avoid real actions.
        However, if this method is called, you must finish all waiting missions. 
        """
        pass 








def pathjoin(*args):
    """
    This is like os.path.join but does some rclone-specific things because there could be
    a ':' in the first part.

    The second argument could be '/file', or 'file' and the first could have a colon.
        pathjoin('a','b')   # a/b
        pathjoin('a:','b')  # a:b
        pathjoin('a:','/b') # a:/b
        pathjoin('a','/b')  # a/b  NOTE that this is different
    """
    if len(args) <= 1:
        return "".join(args)

    root, first, rest = args[0], args[1], args[2:]

    if root.endswith("/"):
        root = root[:-1]

    if root.endswith(":") or first.startswith("/"):
        path = root + first
    else:
        path = f"{root}/{first}"

    path = os.path.join(path, *rest)
    return path