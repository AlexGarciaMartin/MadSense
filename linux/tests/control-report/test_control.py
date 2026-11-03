import copy
import unittest

from stage_control import ACTOR, DEVICE, HELPER, ROOT, RUNNER, TARGET, digest, edits, transform, verify_overlay


class OverlayTests(unittest.TestCase):
    def test_exact_source_transform_changes_only_observation_hooks(self):
        originals={name:(ROOT/name).read_bytes() for name in edits()}
        helper=(ROOT/HELPER).read_bytes()
        changes={}
        for name,data in originals.items():
            text=data.decode().replace('\r\n','\n');modified=transform(name,text)
            restored=modified
            for old,new in reversed(edits()[name]):
                self.assertEqual(restored.count(new),1)
                restored=restored.replace(new,old,1)
            self.assertEqual(restored,text)
            with self.assertRaises(ValueError):transform(name,modified)
            changes[name]=dict(originalSha256=digest(data),instrumentedSha256=digest(modified.encode()))
        changes[TARGET]=dict(originalSha256=None,instrumentedSha256=digest(helper),copiedFrom=HELPER)
        value=dict(schema=1,kind='hid-control-research-copy',productionBinary=False,dispatchModified=False,
                   sourceCommit='a'*40,sourceArchiveSha256='b'*64,changes=changes)
        self.assertTrue(verify_overlay(value,originals,helper))
        for key,bad_value in (('productionBinary',True),('dispatchModified',True),('sourceCommit','unknown')):
            bad=copy.deepcopy(value);bad[key]=bad_value
            with self.assertRaises(ValueError):verify_overlay(bad,originals,helper)
        bad=copy.deepcopy(value);bad['changes'][DEVICE]['instrumentedSha256']='0'*64
        with self.assertRaises(ValueError):verify_overlay(bad,originals,helper)

    def test_changed_or_duplicate_hook_is_rejected(self):
        for name in (DEVICE,ACTOR,RUNNER):
            text=(ROOT/name).read_text();old,_=edits()[name][0]
            for changed in (text.replace(old,'source drift'),text+'\n'+old):
                with self.assertRaises(ValueError):transform(name,changed)


if __name__=='__main__':unittest.main()
