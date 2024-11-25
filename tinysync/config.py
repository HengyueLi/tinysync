configuration = {
                # attributes for comparing files 
                # Options: {'size','mtime','hash'}
                'compare' : "hash", 

                # Some remotes do not support hashes at all (e.g crypt) while others do not
                # always return a hash for all files (e.g. S3). When this is encountered,
                # syncrclone can fall back to another `compare` or `renames{AB}` attribute.
                # Specify as None (default) to instead throw an error.
                'hash_fail_fallback' : None,  # {'size','mtime',None}

                'conflict_mode' : "newer", # If a conflict cannot be resolved it will default to 'tag' and print a warning.

                'always_get_mtime' : True,


                # Will reset the state of the sync pairs. This will assume the two have "
                # "not been synced and the end result is the UNION of the two remotes "
                # "(i.e. no delete propogation, all modified files look like conflicts, etc). "
                # "Also rehashes all files if applicable. It is best to run a regular sync "
                # "and then perform a reset.
                'reset_state':False,
                

                # When backups are set, all overwrites or deletes will instead be backed up (moved into
                # the workdir folder)
                "backup" : True,


                # None: Automatic based on remote support
                # False: Always use move (not suggested)
                # True: Always use copy (safest)
                'backup_with_copy':None,


                'action_threads':2,

                # how to treat symbolic-link. 
                # 0: Ignore link
                # 1: Treate as normal dir. This may cause serious problems if there are links whose targets are inside sync-dir
                # 2: Try to keep link. In this case, backend.mklink method must be concreated.
                'linkMode':2, 

                # By default, syncrclone will set a lock to prevent other sync jobs from
                # working at the same time. Note that other tools will not respect these
                # locks. They are only for syncrclone.
                #
                # They are not strictly needed and require extra rclone calls. As such, they
                # can be disabled. If disabled, will also NOT respect them from other calls
                'set_lock':True,


                ## Logs

                # All output is printed to stdout and stderr but this can also be saved and
                # uploaded to a remote. Note that the last upload step will not be in the logs
                # themselves. The log name is fixed as '{name}_{date}.log'
                #
                # Can also specify a local location. Useful if both remotes are remote. Recall
                # that the paths are relative to this file. If blank, will not save logs
                'save_logs' : True,
                'local_log_dest' : "",  # NOT on a remote


                # How to handle conflicts.
                # Note that even if comparison is done via hash, you can still resolve via
                # mod time. Be aware that not all remotes return reliable mod times and adjust
                # accordingly. See https://rclone.org/overview/
                #
                #   'A','B'             : Always select A or B
                #   'tag'               : Tag both. Makes tag_conflict irrelevant
                #
                #   'older','newer'     : Select the respective file. See note below.
                #   'smaller','larger'  : Select the smaller or larger file
                #
                # If a conflict cannot be resolved it will default to 'tag' and print a warning.
                "conflict_mode" : "newer",

                # You can choose to tag the other file rather than overwrite it. If tagged,
                # it will get renamed to have appended `.{time}.{A or B}` to the file
                'tag_conflict' : False,



                # When doing mtime comparisons, what is the error to allow
                'dt' : 1.1,  # seconds


                # delete old logs and backups 
                'logExpiredDays': 10,


                # this is approximated value. 
                "cacheSizeMB":500,




            }