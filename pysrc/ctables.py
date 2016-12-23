#!/usr/bin/env python
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
import re,types,sys,os
import genutil
import codegen
import enum_txt_writer

# emit XED_OPERAND_CONVERT_ enum
# emit mapping from XED_OPERAND_CONVERT_* enum to the arrays created here


def emit_convert_enum(converts,xeddir='.',gendir='obj'):
   i =  enum_txt_writer.enum_info_t(converts, xeddir, gendir,
                                    'xed-operand-convert',
                                    'xed_operand_convert_enum_t',
                                    'XED_OPERAND_CONVERT_',
                                    cplusplus=False)
   i.print_enum()
   i.run_enumer()
   return [i.src_full_file_name, i.hdr_full_file_name]


class constant_table_t(object):
    """Create string constant lookup tables. Invalid error elements
    are null strings. The inputs must be dense and ordered. FIXME: add
    something that handles dont-cares, sorts, and fills in missing
    entries. The input left hand column is ignored right now, and
    assumed to be binary."""
    match_blank = re.compile(r'^$')
    match_header = \
        re.compile(r'(?P<name>[A-Za-z0-9_]+)[(](?P<operand>[A-Za-z0-9_]+)[)]::')

    match_pair = re.compile(r"""(?P<value>[bxmA-F0-9_]+)[ \t]*[-][>][ \t]*['](?P<symbol>[^']*)[']""")

    match_pair_error = \
        re.compile(r'(?P<value>[bxmA-F0-9_]+)[ \t]*[-][>][ \t]*error')

    def __init__(self):
        self.name=None
        self.operand=None
        self.value_string_pairs=[]
        self.nlines = 0
    def valid(self):
        if self.name != None:
           return True
        return False
    def dump(self):
        print("%s(%s)::" % (self.name, self.operand))
        for (v,p) in self.value_string_pairs:
            if isinstance(p, str):
                print("%s '%s'" % (hex(v),p))
            else:
                print("%s  error" %(hex(v)))

    def emit_init(self):
        lines = []
        self.string_table_name = 'xed_convert_table_%s' % (self.name)
        lines.append('static const char* %s[] = {' % (self.string_table_name))

        for (v,p) in self.value_string_pairs:
            if isinstance(p, str):
                lines.append( '/*%s*/ "%s",' % (hex(v),p))
            else:
                lines.append( '/*%s*/ 0, /* error */' % (hex(v)))
        lines.append('};')
        return lines

        
    def read(self, lines):
        """Read lines from lines until a new header or a blank line is reached"""
        started = False
        while 1:
            if not lines:
                break
            self.nlines += 1            
            line = lines[0]
            line = line.strip()
            line = re.sub(r'#.*','',line)
            m= constant_table_t.match_blank.match(line)
            if m:
                del lines[0]
                continue

            m= constant_table_t.match_header.match(line)
            if m:
                if started:
                    return
                else:
                    started = True
                    del lines[0]
                    self.name = m.group('name')
                    self.operand = m.group('operand')
                    continue
            m = constant_table_t.match_pair.match(line)
            if m:
                value = m.group('value')
                symbol = m.group('symbol')
                numeric_value = genutil.make_numeric(value)
                #print "INPUT: [%s] [%s]" % (value,symbol)
                self.value_string_pairs.append((numeric_value,symbol))
                del lines[0]
                continue
            m = constant_table_t.match_pair_error.match(line)
            if m:
                value = m.group('value')
                numeric_value = genutil.make_numeric(value)
                self.value_string_pairs.append((numeric_value,None))
                del lines[0]
                continue
            else:
                genutil.die("Could not parse line %d: [%s]\n\n" % (self.nlines,line))
                
        
    
    
def work(lines,   xeddir = '.',   gendir = 'obj'):
   tables = []
   while lines:
       y = constant_table_t()
       y.read(lines)
       #y.dump()
       olines = y.emit_init()
       #for l in olines:
       #    print l
       tables.append(y)
       
   tables=[x for x in tables if x.valid()]
   names=[x.name for x in tables]

   srcs = emit_convert_enum(['INVALID'] + names, xeddir, gendir)
   src_file_name = 'xed-convert-table-init.c'
   hdr_file_name = 'xed-convert-table-init.h'
   xfe = codegen.xed_file_emitter_t(xeddir, gendir, src_file_name)
   xfe.add_header(hdr_file_name)
   xfe.start()

   hfe = codegen.xed_file_emitter_t(xeddir,
                                    gendir,
                                    hdr_file_name)
   hfe.start()

   xfe.add_code('xed_convert_table_t xed_convert_table[XED_OPERAND_CONVERT_LAST];')
   
   for t in tables:
       l = t.emit_init()
       l = [x+'\n' for x in l]
       xfe.writelines(l)
   fo = codegen.function_object_t('xed_init_convert_tables', 'void')
   
   s1 = 'xed_convert_table[XED_OPERAND_CONVERT_%s].table_name = %s;' % ('INVALID', '0')
   s2 = 'xed_convert_table[XED_OPERAND_CONVERT_%s].limit      = %s;' % ('INVALID', '0')
   s3 = 'xed_convert_table[XED_OPERAND_CONVERT_%s].opnd       = %s;' % ('INVALID', 'XED_OPERAND_INVALID')
   fo.add_code(s1)
   fo.add_code(s2)
   fo.add_code(s3)
   
   for t in tables:
       s1 = 'xed_convert_table[XED_OPERAND_CONVERT_%s].table_name = %s;' % (t.name, t.string_table_name)
       s2 = 'xed_convert_table[XED_OPERAND_CONVERT_%s].limit      = %s;' % (t.name, len(t.value_string_pairs))
       s3 = 'xed_convert_table[XED_OPERAND_CONVERT_%s].opnd       = %s;' % (t.name, t.operand)
       fo.add_code(s1)
       fo.add_code(s2)
       fo.add_code(s3)

   fo.emit_file_emitter(xfe)
   xfe.close()

   hdr = []
   hdr.append("typedef struct {\n")
   hdr.append("   const char** table_name;\n")
   hdr.append("   xed_operand_enum_t opnd;\n") # which operand indexes the table!
   hdr.append("   unsigned int limit;\n")
   hdr.append("} xed_convert_table_t;")
   hdr.append("extern xed_convert_table_t xed_convert_table[XED_OPERAND_CONVERT_LAST];")
   hfe.writelines([x+'\n' for x in hdr])
   hfe.close()

   srcs.append(hfe.full_file_name)
   srcs.append(xfe.full_file_name)
   return srcs

if __name__ == '__main__':
   import sys
   lines = open(sys.argv[1],'r').readlines()
   srcs = work(lines,xeddir='.',gendir='obj')
   print("WROTE: ", "\n\t".join(srcs))
