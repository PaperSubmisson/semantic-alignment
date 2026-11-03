public class StringBuilderCapLen02 {
  public static boolean f(String arg){
    StringBuilder buffer = new StringBuilder(arg);
    return buffer.toString().equals("Diffblue  is leader in automatic test case generation");
  }
}
