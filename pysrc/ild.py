#BEGIN_LEGAL
#
#Copyright (c) 2016 Intel Corporation
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#  
#END_LEGAL
import sys
import re
import copy
import genutil
import ildutil
import mbuild
import ild_info
import ild_storage
import ild_storage_data
import ild_eosz
import ild_easz
import ild_nt
import ild_imm
import ild_disp
import ild_modrm
import codegen
import os
import collections
import shutil
import ild_phash
import ild_codegen
import math
import opnds
import ild_cdict
import xed3_nt
import actions
import verbosity
import imp


op_bin_pattern = re.compile(r'[_10]{2,}$')
op_hex_pattern = re.compile(r'[0-9a-f]{2}$', flags=re.IGNORECASE)
reg_binding_pattern = re.compile(r'REG[[](?P<bits>0b[01_]+)]')
mod_eq_pattern = re.compile(r'MOD=(?P<bits>[0123]{1})')
mod_neq_pattern = re.compile(r'MOD(!=|=!)(?P<bits>[0123]{1})')


#debugdir is updated in init_debug
#the module.
debugdir = '.'

#the debug file
debug = None

storage_fn = 'ild_storage_data.py'

#FIXME: can we get it from generator.py?
_xed_3dnow_category = '3DNOW'

_mode_token = 'MODE'

_vexvalid_op = 'VEXVALID'


#checks if we have 3dnow instructions
#some of these instructions have 0f 0f ... opcode pattern
#hence we need to discard second 0f and not treat it as opcode
#this is a special case, there is no way to treat it in a general way
#hence we need to check for it
def _is_amd3dnow(agi):
    for g in agi.generator_list:
        ii = g.parser_output.instructions[0]
        if genutil.field_check(ii,'iclass'):
            for ii in g.parser_output.instructions:
                if ii.category == _xed_3dnow_category:
                    return True
    return False

#just to know how many there are and how hard it is
#42 nested NTs..
#mostly modrm-related
def _get_nested_nts(agi):
    nested_nts = set()
    for nt_name in list(agi.nonterminal_dict.keys()):
        g = agi.generator_dict[nt_name]
        ii = g.parser_output.instructions[0]
        if genutil.field_check(ii,'iclass'):
            continue #only real NTs, not instructions
        for rule in g.parser_output.instructions:
            for bt in rule.ipattern.bits:
                if bt.is_nonterminal():
                    nested_nts.add(nt_name)
            for op in rule.operands:
                if op.type == 'nt_lookup_fn':
                    nested_nts.add(nt_name)
    return nested_nts


###########################################################################
## < Interface for generator >
###########################################################################

#needed to init the debug directory
def init_debug(agi):
    global debug
    global debugdir
    debugdir = agi.common.options.gendir
    debug = open(mbuild.join(debugdir, 'ild_debug.txt'), 'w')

def gen_xed3(agi,ild_info,is_3dnow,ild_patterns,
            all_state_space,ild_gendir,all_ops_widths):
    all_cnames = set()
    ptrn_dict = {}
    maps = ild_info.get_maps(is_3dnow)
    for insn_map in maps:
            ptrn_dict[insn_map] = collections.defaultdict(list)
    for ptrn in ild_patterns:
        ptrn_dict[ptrn.insn_map][ptrn.opcode].append(ptrn)
    #FIXME:bad name
    vv_lu = {}
    #mapping between a operands to their look up function
    op_lu_map = {}

    for vv in sorted(all_state_space['VEXVALID'].keys()):
        #cdict is a 2D dictionary:
        #cdict[map][opcode] = ild_cdict.constraint_dict_t
        #each constraint_dict_t describes all the patterns that fall
        #into corresponding map-opcode
        #cnames is a set of all constraint names from the patterns
        #in the given vv space
        cdict,cnames = ild_cdict.get_constraints_lu_table(ptrn_dict,
                                                          is_3dnow,
                                                          all_state_space,
                                                          vv,
                                                          all_ops_widths)
        all_cnames = all_cnames.union(cnames)
        _msg("vv%s cnames: %s" % (vv,cnames))

        #now generate the C hash functions for the constraint
        #dictionaries
        (ph_lu,lu_fo_list,operands_lu_list) = ild_cdict.gen_ph_fos(
            agi, 
            cdict,
            is_3dnow,
            mbuild.join(ild_gendir,
                        'all_constraints_vv%s.txt' %vv),
            ptrn_dict, 
            vv)
        #hold only one instance of each function
        for op in operands_lu_list :
            if op.function_name not in op_lu_map:
                op_lu_map[op.function_name] = op

        vv_lu[str(vv)] = (ph_lu,lu_fo_list)
    _msg("all cnames: %s" % all_cnames)
    #dump the hash functions and lookup tables for obtaining these
    #hash functions in the decode time
    ild_codegen.dump_vv_map_lookup(agi,
                                   vv_lu,
                                   is_3dnow,
                                   list(op_lu_map.values()),
                                   h_fn='xed3-phash.h')
    #xed3_nt.work generates all the functions and lookup tables for
    #dynamic decoding
    xed3_nt.work(agi, all_state_space, all_ops_widths, ild_patterns)

#Main entry point of the module
def work(agi):
    ild_gendir = agi.common.options.gendir
    init_debug(agi)
    is_3dnow = _is_amd3dnow(agi)

    debug.write("state_space:\n %s" % agi.common.state_space)
    #return

    debug.write("DUMP STORAGE %s\n" % agi.common.options.gen_ild_storage)

    # Collect up interesting NT names.
    # We are going to use them when we generate pattern_t objects
    # and also when we build resolution functions.
    eosz_nts = ild_eosz.get_eosz_binding_nts(agi)
    easz_nts = ild_easz.get_easz_binding_nts(agi)
    imm_nts = ild_imm.get_imm_binding_nts(agi)

    disp_nts = ild_disp.get_disp_binding_nts(agi)
    brdisp_nts = ild_disp.get_brdisp_binding_nts(agi)

    #just for debugging
    _msg("EOSZ NTS:")
    for nt_name in eosz_nts:
        _msg(nt_name)

    _msg("\nEASZ NTS:")
    for nt_name in easz_nts:
        _msg(nt_name)

    _msg("\nIMMNTS:")
    for nt_name in imm_nts:
        _msg(nt_name)

    _msg("\nDISP NTS:")
    for nt_name in disp_nts:
        _msg(nt_name)

    _msg("\nBRDISP NTS:")
    for nt_name in brdisp_nts:
        _msg(nt_name)

    nested_nts = _get_nested_nts(agi)
    _msg("\nNESTED NTS:")
    for nt_name in nested_nts:
        _msg(nt_name)

    #Get dictionary with all legal values for all interesting operands
    all_state_space = ild_cdict.get_all_constraints_state_space(agi)
    _msg("ALL_STATE_SPACE:")
    for k,v in list(all_state_space.items()):
            _msg("%s: %s"% (k,v))

    #Get widths for the operands
    all_ops_widths = ild_cdict.get_state_op_widths(agi, all_state_space)

    _msg("ALL_OPS_WIDTHS:")
    for k,v in list(all_ops_widths.items()):
            _msg("%s: %s"% (k,v))

    #generate a list of pattern_t objects that describes the ISA.
    #This is the main data structure for XED3
    ild_patterns = get_patterns(agi, is_3dnow, eosz_nts, easz_nts, imm_nts,
                                disp_nts, brdisp_nts, all_state_space)

    if ild_patterns:
        if agi.common.options.gen_ild_storage:
            #dump the ild_storage_data.py file
            emit_gen_info_lookup(agi, ild_patterns, is_3dnow, debug)
            imp.reload(ild_storage_data)

        #get ild_storage_t object - the main data structure for ILD
        #essentially a 2D dictionary:
        #united_lookup[map][opcode] == [ ild_info_t ]
        #the ild_info_t objects are obtained both from grammar and
        #ild_storage_data.py file, so that if ild_info_t objects are
        #defined in ild_storage_data.py file, ILD will have information
        #about illegal map-opcodes too.
        united_lookup = _get_united_lookup(ild_patterns,is_3dnow)

        #generate modrm lookup tables
        ild_modrm.work(agi, united_lookup, debug)

        #dump_patterns is for debugging
        if verbosity.vild():
            dump_patterns(ild_patterns,
                          mbuild.join(ild_gendir, 'all_patterns.txt'))


        eosz_dict = ild_eosz.work(agi, united_lookup,
                    eosz_nts, ild_gendir, debug)

        easz_dict = ild_easz.work(agi, united_lookup,
                    easz_nts, ild_gendir, debug)

        #dump operand accessor functions
        agi.operand_storage.dump_operand_accessors(agi)
        

        if eosz_dict and easz_dict:
            ild_imm.work(agi, united_lookup, imm_nts, ild_gendir,
                         eosz_dict, debug)
            ild_disp.work(agi, united_lookup, disp_nts, brdisp_nts,
                          ild_gendir, eosz_dict, easz_dict, debug)


        #dump scanners headers - they might be different for different
        #models.
        scanners_dict =  agi.common.ild_scanners_dict
        dump_header_with_header(agi, 'xed-ild-scanners.h', scanners_dict)

        getters_dict =  agi.common.ild_getters_dict
        dump_header_with_header(agi, 'xed-ild-getters.h', getters_dict)

        gen_xed3(agi,ild_info,is_3dnow,ild_patterns,
                 all_state_space,ild_gendir,all_ops_widths)

def dump_header_with_header(agi, fname, header_dict):
    """ emit the header fname.
        add the header in header_dict with the maximal id.
        
        this mechanism is used in order to choose header 
        files in the build time,
        different build configuration use different header files.
        e.g. when building without AVX512 we are using the basic getters.
             when building with AVX512 the header that is used comes 
             form avx512 layer.
             
             FIXME: when all avx512 will move into the base layer 
                    we can removed this 
                    mechanism and use C defines. """
    
    _msg("HEADER DICT: %s" % header_dict)
    
    xeddir = os.path.abspath(agi.common.options.xeddir)
    gendir = mbuild.join(agi.common.options.gendir,'include-private')
    if header_dict:
        header = max(header_dict, key=header_dict.get)
        header = os.path.normcase(header)

        header_basename = os.path.basename(header)

        _msg("HEADER: %s" %header)
        if os.path.exists(header):
            _msg("HEADER EXISTS: %s" %header)
        else:
            _msg("BADNESS - HEADER DOES NOT EXIST: %s" %header)
        try:
            shutil.copyfile(header, os.path.join(gendir, header_basename))
        except:
            ildutil.ild_err("Failed to copyfile src=%s dst=%s"%
                              (header,
                               os.path.join(gendir, header_basename)))
    else:
        header_basename = None
    h_file = codegen.xed_file_emitter_t(xeddir,gendir,
                                fname, shell_file=False,
                                is_private= True)
    if header_basename:
        h_file.add_header(header_basename)
    h_file.start()
    h_file.close()

def get_patterns(agi, is_3dnow, eosz_nts, easz_nts,
                 imm_nts, disp_nts, brdisp_nts, all_state_space):
    """
    This function generates the pattern_t objects that have all the necessary
    information for the ILD. Returns these objects as a list.
    """
    patterns = []
    for g in agi.generator_list:
        ii = g.parser_output.instructions[0]
        if genutil.field_check(ii,'iclass'):
            for ii in g.parser_output.instructions:
                ptrn = pattern_t(ii, is_3dnow, eosz_nts,
                                 easz_nts, imm_nts, disp_nts, brdisp_nts,
                                 ildutil.mode_space, all_state_space)
                patterns.append(ptrn)
                if ptrn.incomplete_opcode:
                    expanded_ptrns = ptrn.expand_opcode()
                    patterns.extend(expanded_ptrns)
    return patterns


def _get_united_lookup(ptrn_list,is_3dnow):
    """
    Combine storage obtained from grammar and from ILD storage
    @return: ild_info.storage_t object
    """
    #build an ild_info_storage_t object from grammar
    from_grammar = get_info_storage(ptrn_list, 0, is_3dnow)

    #get an ild_info_storage_t object from ild python-based storage
    from_storage = ild_storage_data.gen_ild_info()

    #FIXME: should we make is_amd=(is_3dnow or from_storage.is_3dnow)?
    united_lookup = ild_storage.ild_storage_t(is_amd=is_3dnow)

    #unite the lookups, conflicts will be resolved by priority
    for insn_map in ild_info.get_maps(is_3dnow):
        for op in range(0, 256):
            ulist = (from_grammar.get_info_list(insn_map, hex(op)) +
                     from_storage.get_info_list(insn_map, hex(op)))
            united_lookup.set_info_list(insn_map, hex(op), ulist)
    return united_lookup


def get_info_storage(ptrn_list, priority, is_3dnow):
    """
    convert list of pattern_t objects to ild_storage_t object
    """
    storage = ild_storage.ild_storage_t(is_amd=is_3dnow)

    for p in ptrn_list:
        info = ild_info.ptrn_to_info(p, priority)
        if not (info in storage.get_info_list(p.insn_map,p.opcode)):
            storage.append_info(p.insn_map, p.opcode, info)
    return storage


def emit_gen_info_lookup(agi, ptrn_list, is_3dnow, debug):
    debug.write("DUMPING ILD STORAGE\n")
    f = codegen.xed_file_emitter_t(agi.common.options.xeddir,
                                   agi.common.options.xeddir,
                                   storage_fn,
                                   shell_file=True)

    storage = get_info_storage(ptrn_list, ild_info.storage_priority, is_3dnow)
    #list_name = "info_lookup['%s']['%s']"
    list_name = 'info_list'
    indent = ' ' * 4
    f.start()
    f.add_code("import ild_info")
    f.add_code("import ild_storage\n\n\n")

    f.add_code("#GENERATED FILE - DO NOT EDIT\n\n\n")
    f.write("def gen_ild_info():\n")
    f.write(indent + "storage = ild_storage.ild_storage_t(is_amd=%s)\n" %
               is_3dnow)

    for insn_map in ild_info.get_dump_maps():
        for op in range(0, 256):
            for info in storage.get_info_list(insn_map, hex(op)):
                f.write("%s#MAP:%s OPCODE:%s\n" %
                   (indent, info.insn_map, info.opcode))
                f.write("%sinfo_list = storage.get_info_list('%s','%s')\n" %
                           (indent, insn_map, hex(op)))
                emit_add_info_call(info, list_name,
                                   f, indent)
    f.write(indent + "return storage\n")
    f.close()

def emit_add_info_call(info, list_name, f, indent=''):
    s = []
    tab = ' ' * 4
    s.append(indent + "%s.append(ild_info.ild_info_t(\n%sinsn_map='%s'" %
             (list_name,(indent+tab),info.insn_map))
    s.append("opcode='%s'" % info.opcode)
    s.append("incomplete_opcode=%s" % info.incomplete_opcode)
    s.append("missing_bits=%s" % info.missing_bits)
    s.append("has_modrm='%s'" % info.has_modrm)
    s.append("imm_nt_seq=%s" % info.imm_nt_seq)
    s.append("disp_nt_seq=%s" % info.disp_nt_seq)
    s.append("eosz_nt_seq=%s" % info.eosz_nt_seq)
    s.append("easz_nt_seq=%s" % info.easz_nt_seq)
    s.append("ext_opcode=%s" % info.ext_opcode)
    s.append("mode=%s" % info.mode)
    s.append("priority=%s" % ild_info.storage_priority)
    f.write((",\n%s" % (indent + tab)).join(s) + "))\n\n")



#this is for debugging mostly. Also a good source of info on instruction set.
def dump_patterns(patterns, fname):
    f = open(fname, 'w')
    for pattern in patterns:
        f.write( '%s\n' % pattern)
    f.close()

###########################################################################
## </ Interface for generator >
###########################################################################



#Maybe pattern_t should inherit from instruction_info_t
#Let it inherit from object for now.
class pattern_t(object):

    # keys will be in a special order which is why we build it
    # from a list.
    phys_map_keys = []
    phys_map_dir = {}
    first = True

    def __init__(self, ii, is_3dnow, eosz_nts,
                 easz_nts, imm_nts, disp_nts, brdisp_nts, mode_space,
                 state_space):

        # FIXME 2012-06-19 MJC: is there a better way to do complex
        # init of class attributes?
        if pattern_t.first:
            pattern_t.first = False
            self._setup_phys_map(is_3dnow)

        self.ptrn = ii.ipattern_input
        self.ptrn_wrds = self.ptrn.split()
        self.iclass = ii.iclass
        self.legal = True

        #amd 3dnow instructions have nasty 0f 0f ... opcode pattern
        #in which second 0f is not an opcode! This should be treated
        #in a special way
        self.amd3dnow_build = is_3dnow  #this one is NOT used DELETE IT ???

        self.category = ii.category
        #FIXME: remove all members of ii stored directly as members
        self.ii = ii

        #incomplete_opcode is used for expanding opcodes that have registers
        #embedded in them
        self.incomplete_opcode = False

        #number of missing bits in incomplete opcode. usually 0 or 3
        self.missing_bits = 0

        self.insn_map = None
        self.opcode = None

        self.space = None # LEGACY|VEX|EVEX
        self.has_modrm = False

        self.imm_nt_seq = None

        self.disp_nt_seq = None

        #modrm.reg bits value, set only when it is explicitly
        #e.g. bounded: REG[010]
        self.ext_opcode = None

        #all legal values for MODE operand in this pattern
        self.mode = None


        #an ordered string of EOSZ setting NTs in the pattern
        #we will use it to create the eosz lookup table for the pattern
        self.eosz_nt_seq = None


        #same for EASZ
        self.easz_nt_seq = None

        #operand deciders of the pattern
        #FIXME: not finished yet
        self.constraints = collections.defaultdict(dict)


        insn_map,opcode = self.get_map_opcode()
        self.insn_map = insn_map
        self.opcode = opcode

        self.has_modrm = ild_modrm.get_hasmodrm(self.ptrn)
        self.set_ext_opcode()

        self.set_mode(ii, mode_space)


        self.eosz_nt_seq = ild_eosz.get_eosz_nt_seq(self.ptrn_wrds,
                                                         eosz_nts)

        self.easz_nt_seq = ild_easz.get_easz_nt_seq(self.ptrn_wrds,
                                                         easz_nts)

        self.imm_nt_seq = ild_imm.get_imm_nt_seq(self.ptrn_wrds, imm_nts)

        self.disp_nt_seq = ild_disp.get_disp_nt_seq(self.ptrn_wrds,
                                                    disp_nts.union(brdisp_nts))

        self.set_constraints(ii, state_space)
        self.actions = [actions.gen_return_action(ii.inum)]

        #Not implementing this yet.
        #Will implement after code review for has_modrm
        #self.set_hasimm()
        #self.set_pfx_table()

        #FIXME: for anaisys only
        if self.is_3dnow():
            if not self.has_modrm:
                _msg('3DNOW with no MODRM: %s\n' % self)

    def is_3dnow(self):
        return self.category == _xed_3dnow_category

    def is_legal(self):
        return (self.legal and
                self.opcode != None and
                self.insn_map != None and
                self.space != None)


    def set_ext_opcode(self):
       #inline captures MOD[mm] REG[rrr] RM[nnn]
       #or REG[111] etc. -- can be constant
       for w in self.ptrn_wrds:
           pb = reg_binding_pattern.match(w)
           if pb:
               bits = pb.group('bits')
               self.ext_opcode = genutil.make_numeric(bits)
               return

    def set_constraints(self, ii, state_space):
        #FIXME: this assumes, that there are no contradictions
        #between constraints on the same operand.
        #If there are, e.g. MOD=3 ... MOD=1, both values will be
        #set as legal.. check such things here?

        #set constraints that come from operands deciders
        xed3_nt.get_ii_constraints(ii, state_space, self.constraints)
        #print "CONSTRAINTS: {}".format(self.constraints)

        #special care for VEXVALID - it makes it easy to dispatch
        #vex and legacy instructions:
        #for legacy we will explicitly set VEXVALID=0
        if _vexvalid_op not in self.constraints:
            self.constraints[_vexvalid_op] = {0:True}

    def set_mode(self, ii, mode_space):
        for bit_info in ii.ipattern.bits:
            if bit_info.token == _mode_token:
                #self.refining = [111555]
                if bit_info.test == 'eq':
                    self.mode = [bit_info.requirement]
                else:
                    mod_space = copy.deepcopy(mode_space)
                    mod_space.remove(bit_info.requirement)
                    self.mode = mod_space
                return
        #FIXME: when MODE is not mentioned, we assume that any value is
        #legal, it should not mess the conflict resolution.
        #But maybe None is better
        self.mode = ildutil.mode_space

    def parse_opcode(self, op_str):
        val = None
        if genutil.numeric(op_str):
            val = genutil.make_numeric(op_str)

            # special check for partial binary numbers as opcodes
            if genutil.is_binary(op_str):
                bin_str = re.sub('^0b', '', op_str)
                bin_str = re.sub('_', '', bin_str)
                #if it is bin string, it might be incomplete, and we
                #assume that given bits are msb. Hence we have to
                #shift left

                if len(bin_str) > 8:
                    ildutil.ild_err("Unexpectedly long binary opcode: %s" %
                             bin_str)

                if len(bin_str) < 8:
                    self.missing_bits = 8-len(bin_str)
                    val = val << (self.missing_bits)
                    self.incomplete_opcode = True
                    _msg('incomplete opcode for iclass %s, pttrn %s' %
                        (self.iclass, self.ptrn))

        return val

    def err(self, msg):
        self.legal = False
        sys.stderr.write("ILD_PARSER PATTERN ERROR: %s\n\nPattern:\n%s\n" %
                         (msg, self))
        mbuild.msgb("ILD_PARSER ERROR", msg)
        mbuild.msgb("ILD_PARSER", "ABORTED ILD generation")
        #sys.exit(1)

    #if opcode is incomplete, than we have 0's in all the missing bits and
    #need to create copies of the pattern_t that have all other possible
    #variations of the opcode. For example PUSH instruction has opcode 0x50
    #and in expand_opcode method we will create a list of pattern_t objects
    #that have opcodes 0x51-0x57
    #We don't need to create the 0x50 variant, because it already exists
    #(it is the current self)
    #That way we will cover all the legal opcodes for the given incomplete
    #opcode.
    def expand_opcode(self):
        expanded = []
        if self.incomplete_opcode:
            if 'RM[rrr]' in self.ptrn or 'REG[rrr]' in self.ptrn:
                _msg('Expanding opcode for %s' % self.iclass)
                #FIXME: MJC: was assuming hex for self.opcode (2012-06-19)
                opcode = genutil.make_numeric(self.opcode)
                #iterating through all legal variations of the incomplete
                #opcode.
                #Since the variant with all missing bits == 0 was already
                #created (it is the current self), we need to iterate through
                #1 to 2 ^ (self.missing_bits)
                for i in range(1, 2**self.missing_bits):
                    new_ptrn = copy.deepcopy(self)
                    new_ptrn.opcode = hex(opcode | i)
                    expanded.append(new_ptrn)
        return expanded

    def get_opcode(self, tokens):
        opcode = None
        for token in tokens:
            opcode = self.parse_opcode(token)
            if opcode != None:
                break
        if opcode == None:
            ildutil.ild_err("Failed to parse op_str with " +
                            "from tokens %s" %( tokens))
        return hex(opcode)

    def _setup_phys_map(self,include_amd):
        phys_map_list = [
            ('0x0F 0x38','0x0F38'),
            ('0x0F 0x3A','0x0F3A'),
            ('V0F38','0x0F38'),
            ('V0F3A','0x0F3A'),
            # V0F must be after the V0F38 & V0F3A
            ('V0F','0x0F'), 
            ('MAP5','MAP5'),
            ('MAP6','MAP6') ]
            
        # The AMD map must be  before the naked 0x0F map
        if include_amd:
            phys_map_list.append(('0x0F 0x0F','0x0F0F'))
            phys_map_list.append(('XMAP8','XMAP8'))
            phys_map_list.append(('XMAP9','XMAP9'))
            phys_map_list.append(('XMAPA','XMAPA'))

        # and finally Map 1 ...
        phys_map_list.append(('0x0F','0x0F'))

        # keys will be in a special order which is why we build it
        # from a list.
        pattern_t.phys_map_keys = []
        pattern_t.phys_map_dir = {}

        for a,b in phys_map_list:
            pattern_t.phys_map_keys.append(a)
            pattern_t.phys_map_dir[a] = b

    def get_map_opcode(self):
        insn_map = '0x0'
        s = self.ptrn
        for mpat in  pattern_t.phys_map_keys:
            if s.find(mpat) != -1:
                insn_map = pattern_t.phys_map_dir[mpat]
                # remove the first matching map-like thing
                s = re.sub(mpat,'',s, count=1)
                break

        tokens = s.split()
        opcode = self.get_opcode(tokens)
        return insn_map, opcode

    def has_emit_action(self):
        ''' This function is needed in order to match the interface of rule_t
            it has no real meaning for the docoder '''
        return False

    def __str__(self):
        printed_members = []
        printed_members.append('ICLASS\t: %s' % self.iclass)
        printed_members.append('PATTERN\t: %s' % self.ptrn)
        #putting map and opcode in one line for easier grepping
        #of the pattern list dump - very handy for different kinds of analysis
        printed_members.append('MAP:%s OPCODE:%s' %
                               (self.insn_map, self.opcode))
        printed_members.append('EXT_OPCODE\t: %s' % self.ext_opcode)
        printed_members.append('MODE\t: %s' % self.mode)
        printed_members.append('INCOMPLETE_OPCODE\t: %s' %
                                self.incomplete_opcode)
        printed_members.append('HAS_MODRM\t: %s' % self.has_modrm)
        printed_members.append('EOSZ_SEQ:\t %s' % self.eosz_nt_seq)
        printed_members.append('EASZ_SEQ:\t %s' % self.easz_nt_seq)
        printed_members.append('IMM_SEQ\t: %s' % self.imm_nt_seq)
        printed_members.append('DISP_SEQ\t: %s' % self.disp_nt_seq)
        printed_members.append('CONSTRAINTS\t: %s' % self.constraints)
        printed_members.append('INUM\t: %s' % self.ii.inum)

        return "{\n"+ ",\n".join(printed_members) + "\n}"

    def __eq__(self, other):
        return (other != None and
                self.ptrn == other.ptrn and
                self.iclass == other.iclass)

    def __ne__(self, other):
        return (other == None or
                self.ptrn != other.ptrn or
                self.iclass != other.iclass)

def _msg(s):
    debug.write("%s\n" % (s))

