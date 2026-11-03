import java.io.*;
public class CharacterTable {
  static boolean test(String name, char c) {
    return switch(name) {
      case "isDefined" -> Character.isDefined(c);
      case "isDigit" -> Character.isDigit(c);
      case "isJavaIdentifierStart" -> Character.isJavaIdentifierStart(c);
      case "isJavaIdentifierPart" -> Character.isJavaIdentifierPart(c);
      case "isLetter" -> Character.isLetter(c);
      case "isLetterOrDigit" -> Character.isLetterOrDigit(c);
      case "isLowerCase" -> Character.isLowerCase(c);
      case "isUpperCase" -> Character.isUpperCase(c);
      default -> throw new IllegalArgumentException(name);
    };
  }
  public static void main(String[] args) throws Exception {
    try(PrintWriter out=new PrintWriter(args[0])) {
      out.println("{\"jdk\":\""+System.getProperty("java.version")+"\",\"tables\":{");
      String[] names={"isDefined","isDigit","isJavaIdentifierStart","isJavaIdentifierPart","isLetter","isLetterOrDigit","isLowerCase","isUpperCase","toUpperCase","toLowerCase"};
      for(int m=0;m<names.length;m++) {
        String name=names[m];out.print("\""+name+"\":[");boolean first=true;
        for(int i=0;i<65536;) {
          int start=i;
          int value=m<8?(test(name,(char)i)?1:0):((m==8?Character.toUpperCase((char)i):Character.toLowerCase((char)i))-i);
          i++;
          while(i<65536) {
            int next=m<8?(test(name,(char)i)?1:0):((m==8?Character.toUpperCase((char)i):Character.toLowerCase((char)i))-i);
            if(next!=value)break;i++;
          }
          if(value!=0) {if(!first)out.print(",");first=false;out.print("["+start+","+(i-1)+","+value+"]");}
        }
        out.println("]"+(m==names.length-1?"":","));
      }
      out.println("}}");
    }
  }
}
