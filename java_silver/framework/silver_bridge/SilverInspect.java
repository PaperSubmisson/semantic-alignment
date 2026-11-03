import java.nio.file.*;
import java.util.*;
import java.lang.reflect.*;

/** Reflection keeps compilation independent of the bundled Scala compiler ABI.
 * Uses the real Silver frontend for parsing, name resolution and type checking.
 */
public class SilverInspect {
  static Object invoke(Object x,String name,Object... args) throws Exception {
    for(Method m:x.getClass().getMethods()) {
      if(!m.getName().equals(name)||m.getParameterCount()!=args.length)continue;
      boolean ok=true;
      for(int i=0;i<args.length;i++)if(args[i]!=null&&!m.getParameterTypes()[i].isInstance(args[i])&&!m.getParameterTypes()[i].isPrimitive())ok=false;
      if(ok)return m.invoke(x,args);
    }
    throw new NoSuchMethodException(x.getClass()+"."+name);
  }
  static Object optional(Object x,String name) {
    try{return invoke(x,name);}catch(Exception e){return null;}
  }
  static List<Object> seq(Object s) throws Exception {
    List<Object> out=new ArrayList<>(); Object it=invoke(s,"iterator");
    while((Boolean)invoke(it,"hasNext"))out.add(invoke(it,"next"));
    return out;
  }
  static String quote(String s) {
    StringBuilder b=new StringBuilder("\"");
    for(char c:s.toCharArray()) switch(c){case '\\':b.append("\\\\");break;case '"':b.append("\\\"");break;case '\n':b.append("\\n");break;case '\r':b.append("\\r");break;case '\t':b.append("\\t");break;default:if(c<32)b.append(String.format("\\u%04x",(int)c));else b.append(c);}
    return b.append('"').toString();
  }
  static String json(Object x) {
    if(x==null)return "null";
    if(x instanceof Number||x instanceof Boolean)return x.toString();
    if(x instanceof Map){List<String> v=new ArrayList<>();for(Object k:((Map<?,?>)x).keySet())v.add(quote(k.toString())+":"+json(((Map<?,?>)x).get(k)));return "{"+String.join(",",v)+"}";}
    if(x instanceof List){List<String> v=new ArrayList<>();for(Object y:(List<?>)x)v.add(json(y));return "["+String.join(",",v)+"]";}
    return quote(x.toString());
  }
  static Map<String,Object> node(Object n) throws Exception {
    Map<String,Object> out=new LinkedHashMap<>();
    out.put("kind",n.getClass().getSimpleName());
    out.put("text",n.toString());
    for(String k:new String[]{"name","typ"}){Object v=optional(n,k);if(v!=null)out.put(k,v.toString());}
    Object pos=optional(n,"pos");
    if(pos!=null){
      Map<String,Object> p=new LinkedHashMap<>();p.put("raw",pos.toString());
      for(String k:new String[]{"line","column"}){Object v=optional(pos,k);if(v!=null)p.put(k,v);}
      Object end=optional(pos,"end");
      if(end!=null&&Boolean.TRUE.equals(optional(end,"isDefined"))){Object e=invoke(end,"get");p.put("end_line",optional(e,"line"));p.put("end_column",optional(e,"column"));}
      out.put("position",p);
    }
    // Named child collections permit structural guards without parsing pretty text.
    for(String k:new String[]{"pres","posts","invs","formalArgs","formalReturns","scopedDecls","stmts"}){
      Object v=optional(n,k);
      if(v!=null){List<Object> ns=new ArrayList<>();for(Object a:seq(v))ns.add(node(a));out.put(k,ns);}
    }
    Object body=optional(n,"body");
    if(body!=null){
      Object def=optional(body,"isDefined");
      if(def instanceof Boolean){out.put("has_body",def);if((Boolean)def)out.put("body",node(invoke(body,"get")));}
      else out.put("body",node(body));
    }
    List<Object> children=new ArrayList<>();Object sub=optional(n,"subnodes");
    if(sub!=null)for(Object a:seq(sub))children.add(node(a));
    out.put("children",children);return out;
  }
  public static void main(String[] args) throws Exception {
    Map<String,Object> out=new LinkedHashMap<>();
    Object frontend=Class.forName("viper.silicon.SiliconRunnerInstance").getConstructor().newInstance();
    List<String> cli=Arrays.asList("--plugin","viper.silver.plugin.standard.termination.TerminationPlugin",args[0]);
    Class<?> conv=Class.forName("scala.jdk.javaapi.CollectionConverters");
    Object buf=conv.getMethod("asScala",java.util.List.class).invoke(null,cli);
    Object scalaArgs=invoke(buf,"toSeq");
    try{
      invoke(frontend,"setVerifier",invoke(frontend,"createVerifier","java_alignment"));
      boolean ready=(Boolean)invoke(frontend,"prepare",scalaArgs);
      if(ready){
        invoke(frontend,"init",invoke(frontend,"verifier"));
        invoke(frontend,"reset",Path.of(args[0]).toAbsolutePath());
        invoke(frontend,"runTo",invoke(frontend,"Translation"));
      }
      List<Object> errors=new ArrayList<>();for(Object e:seq(invoke(frontend,"errors")))errors.add(e.toString());
      out.put("errors",errors);
      Object program=invoke(frontend,"program");
      boolean valid=ready&&errors.isEmpty()&&(Boolean)invoke(program,"isDefined");
      out.put("ok",valid);
      if(valid){Object p=invoke(program,"get");
        for(String k:new String[]{"domains","fields","functions","predicates","methods","extensions"}){
          Object v=optional(p,k);if(v==null)continue;
          List<Object> ns=new ArrayList<>();for(Object n:seq(v))ns.add(node(n));out.put(k,ns);
        }
      }
    }catch(Throwable e){out.put("ok",false);out.put("exception",e.toString());e.printStackTrace(System.err);}
    Files.writeString(Path.of(args[1]),json(out));
    // Frontend creation may own thread pools; no verifier execution is requested.
    System.exit(Boolean.TRUE.equals(out.get("ok"))?0:2);
  }
}
